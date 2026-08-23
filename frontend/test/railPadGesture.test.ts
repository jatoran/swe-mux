import assert from 'node:assert/strict'
import test from 'node:test'

import {
  createRailPadGesture,
  RAIL_PAD_DEAD_RADIUS_PX,
  RAIL_PAD_DIAL_DELAY_MS,
  RAIL_PAD_EXIT_RATIO,
  RAIL_PAD_FAN_SPAN_DEG,
  RAIL_PAD_FAN_START_DEG,
  RAIL_PAD_MIN_SCALE,
  RAIL_PAD_OUTER_PX,
  RAIL_PAD_REPEAT_DELAY_MS,
  RAIL_PAD_REPEAT_INTERVAL_MS,
  RAIL_PAD_RING_PX,
  RAIL_PAD_SINGLE_OUTER_PX,
  RAIL_PAD_SKIRT_DEG,
  RAIL_PAD_SWITCH_MARGIN_PX,
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
  let now = 0
  const timers: { at: number; run: () => void; id: number }[] = []
  let nextId = 1
  const gesture = createRailPadGesture<number>(
    {
      fire: slot => fired.push(slot),
      latch: (slot, detail) => latches.push({ slot, armed: detail.armed }),
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
  const press = (x = 0, y = 0) => gesture.press(1, x, y, {
    wedges: 3,
    rings: 1,
    slots: THREE,
    ...options,
  })
  return { gesture, fired, latches, ends, advance, press }
}

/** A point at a distance and an angle, in the gesture's own screen-axis convention. */
const at = (radius: number, degrees: number) => ({
  dx: radius * Math.cos(degrees * Math.PI / 180),
  dy: -radius * Math.sin(degrees * Math.PI / 180),
})

const shapeOf = (wedges: number, rings: number, scale = 1): RailPadShape =>
  ({ wedges, rings, bands: railPadBands(rings, scale) })

const resolveAt = (shape: RailPadShape, radius: number, degrees: number, current: string | null = null) => {
  const point = at(radius, degrees)
  return railPadResolve(point.dx, point.dy, shape, current)
}

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
  const bands = railPadBands(1)
  const radius = bands.dead + (bands.outer - bands.dead) * 0.55
  for (let wedges = 1; wedges <= RAIL_PAD_MAX_WEDGES; wedges += 1) {
    const arc = radius * (RAIL_PAD_FAN_SPAN_DEG / wedges) * Math.PI / 180
    assert.ok(arc >= 44, `${wedges} wedges gives ${Math.round(arc)}px of arc`)
  }
})

test('a three-wedge pad reads left, up and right, and never a fourth', () => {
  const shape = shapeOf(3, 1)
  assert.equal(resolveAt(shape, 60, 0), RIGHT)
  assert.equal(resolveAt(shape, 60, 90), UP)
  assert.equal(resolveAt(shape, 60, 180), LEFT)
  // Everything downward is the centre, which is what makes "pull down" a cancel that always
  // has room - the one gesture a rail on the screen's bottom edge can always complete.
  assert.equal(resolveAt(shape, 60, -90), null)
  assert.equal(resolveAt(shape, 200, -90), null)
})

test('a four-wedge pad splits the same fan four ways, all of them upward', () => {
  const shape = shapeOf(4, 1)
  const seen = [10, 60, 120, 170].map(degrees => resolveAt(shape, 60, degrees))
  assert.deepEqual(seen, [W(0), W(1), W(2), W(3)])
  assert.equal(resolveAt(shape, 60, -90), null)
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
  assert.equal(railPadScaleFor(2, Infinity), 1)
  assert.equal(railPadScaleFor(2, RAIL_PAD_OUTER_PX * 2), 1)
  assert.equal(railPadScaleFor(2, RAIL_PAD_OUTER_PX / 2), 0.5)
  assert.equal(railPadScaleFor(2, 0), RAIL_PAD_MIN_SCALE)
  assert.equal(railPadScaleFor(1, RAIL_PAD_SINGLE_OUTER_PX / 2), 0.5)
  const squeezed = railPadBands(2, 0.5)
  assert.equal(squeezed.ring, RAIL_PAD_RING_PX / 2)
  assert.equal(squeezed.outer, RAIL_PAD_OUTER_PX / 2)
  // The dead radius is not scaled: it is already small, and shrinking it would make the pad
  // fire on a press that never really moved.
  assert.equal(squeezed.dead, RAIL_PAD_DEAD_RADIUS_PX)
})

test('a latched wedge costs the switch margin to leave, at every count', () => {
  for (const wedges of [3, 4, 5]) {
    const shape = shapeOf(wedges, 1)
    const boundary = railPadWedgeBounds(0, wedges).to
    assert.equal(resolveAt(shape, 60, boundary + 1, W(0)), W(0), `${wedges} wedges: held`)
    assert.equal(resolveAt(shape, 60, boundary + 25, W(0)), W(1), `${wedges} wedges: switched`)
    // With nothing latched the same point reads as its raw wedge.
    assert.equal(resolveAt(shape, 60, boundary + 1, null), W(1))
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
// The gesture
// ---------------------------------------------------------------------------

test('a press fires nothing, and crossing the dead radius fires once immediately', () => {
  const { gesture, fired, advance, press } = harness()
  assert.equal(press(100, 100), true)
  assert.deepEqual(fired, [])
  gesture.move(1, 100, 100 - (RAIL_PAD_DEAD_RADIUS_PX - 1))
  assert.deepEqual(fired, [])
  // Crossing it fires on entry, with no clock involved at all - the wedges are thumb-sized
  // but the *commitment* is still this close in, which is what keeps the pad fast.
  gesture.move(1, 100, 100 - RAIL_PAD_DEAD_RADIUS_PX)
  assert.deepEqual(fired, [UP])
  advance(0)
  assert.deepEqual(fired, [UP])
  gesture.cancel()
})

test('the dial delay is not a gesture delay', () => {
  assert.ok(RAIL_PAD_DIAL_DELAY_MS > 0)
  const { gesture, fired, press } = harness()
  press(0, 0)
  gesture.move(1, 0, -60)
  assert.deepEqual(fired, [UP])
  gesture.cancel()
})

test('a hold repeats at the shared cadence, and only an enter-repeat slot does', () => {
  const { gesture, fired, advance, press } = harness()
  press(0, 0)
  gesture.move(1, 0, -60)
  assert.deepEqual(fired, [UP])
  advance(RAIL_PAD_REPEAT_DELAY_MS - 1)
  assert.deepEqual(fired, [UP])
  advance(1)
  assert.deepEqual(fired, [UP, UP])
  advance(RAIL_PAD_REPEAT_INTERVAL_MS * 3)
  assert.equal(fired.length, 5)
  // Switching to a plain `enter` slot fires once and stops repeating.
  gesture.move(1, 60, 0)
  const afterSwitch = fired.length
  assert.equal(fired[afterSwitch - 1], RIGHT)
  advance(RAIL_PAD_REPEAT_DELAY_MS + RAIL_PAD_REPEAT_INTERVAL_MS * 5)
  assert.equal(fired.length, afterSwitch)
  gesture.cancel()
})

test('leaving and re-entering a wedge fires it again', () => {
  const { gesture, fired, press } = harness()
  press(0, 0)
  gesture.move(1, 0, -60)
  gesture.move(1, 0, -(RAIL_PAD_DEAD_RADIUS_PX * RAIL_PAD_EXIT_RATIO) + 0.5)
  gesture.move(1, 0, -60)
  gesture.move(1, 0, 0)
  gesture.move(1, 0, -60)
  assert.deepEqual(fired, [UP, UP, UP])
  gesture.cancel()
})

test('pulling down into the abort zone releases the latch and fires nothing', () => {
  const { gesture, fired, latches, press } = harness()
  press(0, 0)
  gesture.move(1, 0, -60)
  assert.deepEqual(fired, [UP])
  gesture.move(1, 0, 120)
  assert.equal(latches.at(-1)?.slot, null)
  gesture.release(1)
  assert.deepEqual(fired, [UP], 'and the centre does not sneak in on the way out')
})

test('hysteresis is asymmetric: leaving costs less travel than entering', () => {
  const { gesture, fired, press } = harness()
  press(0, 0)
  gesture.move(1, 0, -RAIL_PAD_DEAD_RADIUS_PX)
  assert.deepEqual(fired, [UP])
  gesture.move(1, 0, -(RAIL_PAD_DEAD_RADIUS_PX - 1))
  gesture.move(1, 0, -RAIL_PAD_DEAD_RADIUS_PX)
  assert.deepEqual(fired, [UP])
  gesture.cancel()
})

test('a release slot waits for the lift, and dragging back out cancels it', () => {
  const held = harness()
  held.press(0, 0)
  held.gesture.move(1, -60, 0)
  assert.deepEqual(held.fired, [], 'a release slot fires nothing on the way in')
  assert.equal(held.latches.at(-1)?.armed, true)
  held.gesture.release(1)
  assert.deepEqual(held.fired, [LEFT])

  const escaped = harness()
  escaped.press(0, 0)
  escaped.gesture.move(1, -60, 0)
  escaped.gesture.move(1, 0, 0)
  escaped.gesture.release(1)
  assert.deepEqual(escaped.fired, [])

  const swapped = harness()
  swapped.press(0, 0)
  swapped.gesture.move(1, -60, 0)
  swapped.gesture.move(1, 60, 0)
  swapped.gesture.release(1)
  assert.deepEqual(swapped.fired, [RIGHT])
})

test('the press announces its end however it finished, so the dial can be torn down', () => {
  const lifted = harness()
  lifted.press(0, 0)
  lifted.gesture.move(1, 0, -60)
  assert.deepEqual(lifted.ends, [])
  lifted.gesture.release(1)
  assert.equal(lifted.ends.length, 1)

  const cancelled = harness()
  cancelled.press(0, 0)
  cancelled.gesture.move(1, 0, -60)
  cancelled.gesture.cancel()
  assert.equal(cancelled.ends.length, 1)
  cancelled.gesture.cancel()
  assert.equal(cancelled.ends.length, 1)
})

test('returning to the centre to abort runs nothing, even with a centre bound', () => {
  const { gesture, fired, press } = harness({ slots: { ...THREE, [PAD_CENTER]: { mode: 'enter' } } })
  press(0, 0)
  gesture.move(1, -60, 0)
  gesture.move(1, 0, 0)
  gesture.release(1)
  assert.deepEqual(fired, [])
})

test('a press that never travels fires the centre, and one that did does not', () => {
  const tapped = harness({ slots: { ...THREE, [PAD_CENTER]: { mode: 'enter' } } })
  tapped.press(0, 0)
  tapped.gesture.move(1, 2, 2)
  tapped.gesture.release(1)
  assert.deepEqual(tapped.fired, [PAD_CENTER])
  assert.equal(tapped.gesture.consumeHandledClick(), true, 'the centre it fired is the tap, so the click is spent')

  const dragged = harness({ slots: { ...THREE, [PAD_CENTER]: { mode: 'enter' } } })
  dragged.press(0, 0)
  dragged.gesture.move(1, 60, 0)
  dragged.gesture.release(1)
  assert.deepEqual(dragged.fired, [RIGHT], 'a wedge was chosen, so the centre is not also run')
})

test('an unbound wedge latches, fires nothing, and blocks the centre', () => {
  const { gesture, fired, press, latches } = harness({
    slots: { [UP]: { mode: 'enter' }, [LEFT]: { mode: 'enter' }, [PAD_CENTER]: { mode: 'enter' } },
  })
  press(0, 0)
  gesture.move(1, 60, 0)
  assert.deepEqual(fired, [])
  assert.equal(latches.at(-1)?.slot, RIGHT)
  gesture.release(1)
  assert.deepEqual(fired, [], 'releasing into a dead wedge is a deliberate abort, not a centre tap')
})

test('a disabled slot is a dead wedge, not a hidden one', () => {
  const { gesture, fired, press, latches } = harness({
    slots: { [UP]: { mode: 'enter', disabled: true }, [RIGHT]: { mode: 'enter' } },
  })
  press(0, 0)
  gesture.move(1, 0, -60)
  assert.equal(latches.at(-1)?.slot, UP, 'the wedge is still where it was')
  assert.deepEqual(fired, [])
  gesture.move(1, 60, 0)
  assert.deepEqual(fired, [RIGHT])
  gesture.cancel()
})

test('a cancel fires nothing, including from an armed release slot', () => {
  const { gesture, fired, press } = harness()
  press(0, 0)
  gesture.move(1, -60, 0)
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
  const { gesture, fired, press } = harness({ slots: { [UP]: { mode: 'enter' } } })
  press(0, 0)
  assert.equal(pointerDragOwnsPointer(), false, 'nothing claimed yet: this pad has an axis to give away')
  gesture.move(1, RAIL_PAN_SLOP_PX, 0)
  assert.equal(pointerDragOwnsPointer(), false)
  // The press is over for the pad: coming back vertically must not steal a pan already begun.
  gesture.move(1, 0, -60)
  assert.deepEqual(fired, [])
})

test('a vertical-only pad still claims once the drag goes its way', () => {
  const { gesture, fired, press } = harness({ slots: { [UP]: { mode: 'enter' } } })
  press(0, 0)
  gesture.move(1, 0, -RAIL_PAN_SLOP_PX)
  assert.equal(pointerDragOwnsPointer(), true)
  gesture.move(1, 0, -60)
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
  const { gesture, press } = harness()
  press(0, 0)
  // Claiming the pointer is not by itself an answer to the click. A chip that taps *and*
  // pads has already claimed at this point and must still be tappable.
  assert.equal(gesture.consumeHandledClick(), false)
  gesture.move(1, 0, -60)
  gesture.release(1)
  assert.equal(gesture.consumeHandledClick(), true)
  assert.equal(gesture.consumeHandledClick(), false, 'one-shot')

  press(0, 0)
  gesture.move(1, 0, -60)
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
    roomAbovePx: RAIL_PAD_OUTER_PX / 2,
  })
  press(0, 0)
  const { bands } = gesture.peek()
  assert.equal(bands.ring, RAIL_PAD_RING_PX / 2)
  assert.equal(bands.outer, RAIL_PAD_OUTER_PX / 2)
  gesture.cancel()
})
