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
  type RailPadSlotKey,
} from '../src/railPadGesture.ts'
import { padDirections, padRingOf, padSectorCount } from '../src/commandRail.ts'
import { markPointerDragClaims, pointerDragOwnsPointer } from '../src/pointerDragClaim.ts'
import { RAIL_PAN_SLOP_PX } from '../src/railOverflow.ts'

const CARDINAL_SLOTS: RailPadPressOptions['slots'] = {
  up: { mode: 'enter-repeat' },
  right: { mode: 'enter' },
  left: { mode: 'release' },
}

// A hand-driven clock, so the cadence is asserted rather than waited for.
function harness(options?: Partial<RailPadPressOptions>) {
  const fired: RailPadSlotKey[] = []
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
    orientation: 'cardinal',
    slots: CARDINAL_SLOTS,
    ...options,
  })
  return { gesture, fired, latches, ends, advance, press }
}

/** A point at a distance and an angle, in the gesture's own screen-axis convention. */
const at = (radius: number, degrees: number) => ({
  dx: radius * Math.cos(degrees * Math.PI / 180),
  dy: -radius * Math.sin(degrees * Math.PI / 180),
})

// ---------------------------------------------------------------------------
// The fan
// ---------------------------------------------------------------------------

test('the fan opens upward, and everything below it is the abort zone', () => {
  // 180 degrees above the finger plus a skirt at each end, and nothing else. This is the
  // whole answer to a rail that sits on the bottom edge of the screen: there is no downward
  // wedge to be drawn off the glass or dragged into a place the thumb cannot reach.
  assert.equal(RAIL_PAD_FAN_START_DEG, -RAIL_PAD_SKIRT_DEG)
  assert.equal(RAIL_PAD_FAN_SPAN_DEG, 180 + RAIL_PAD_SKIRT_DEG * 2)
  for (const orientation of ['cardinal', 'diagonal'] as const) {
    // Straight up is a wedge in both, whichever wedge that happens to be.
    assert.notEqual(railPadWedgeIndex(orientation, 90), null)
    // Straight down is past both ends of the fan, whichever way it is measured.
    assert.equal(railPadWedgeIndex(orientation, railPadAngle(0, 100)), null)
    assert.equal(railPadWedgeIndex(orientation, railPadAngle(-40, 100)), null)
    assert.equal(railPadWedgeIndex(orientation, railPadAngle(40, 100)), null)
  }
})

test('the skirt catches a sideways flick that dips below the horizontal', () => {
  // A thumb flicking right does not stay on the horizontal, and the wedge it aimed at has
  // to be the one it gets.
  const dipped = at(60, -RAIL_PAD_SKIRT_DEG + 2)
  assert.equal(railPadWedgeIndex('cardinal', railPadAngle(dipped.dx, dipped.dy)), 0)
  const past = at(60, -RAIL_PAD_SKIRT_DEG - 2)
  assert.equal(railPadWedgeIndex('cardinal', railPadAngle(past.dx, past.dy)), null)
})

test('a cardinal pad is three wedges and a diagonal one is two, both spanning the whole fan', () => {
  for (const orientation of ['cardinal', 'diagonal'] as const) {
    const count = padSectorCount(orientation)
    const first = railPadWedgeBounds(orientation, 0)
    const last = railPadWedgeBounds(orientation, count - 1)
    assert.equal(first.from, RAIL_PAD_FAN_START_DEG)
    assert.equal(last.to, RAIL_PAD_FAN_START_DEG + RAIL_PAD_FAN_SPAN_DEG)
    // Contiguous: no gap between wedges for a drag to fall into.
    for (let index = 1; index < count; index += 1) {
      assert.equal(railPadWedgeBounds(orientation, index).from, railPadWedgeBounds(orientation, index - 1).to)
    }
  }
  assert.equal(padSectorCount('cardinal'), 3)
  assert.equal(padSectorCount('diagonal'), 2)
})

test('wedges are wide enough for a thumb at the radius their labels sit at', () => {
  // The complaint this replaced was that the targets were too small to hit with a thumb.
  // Asserted as an arc length rather than an angle, because that is what a finger meets.
  for (const orientation of ['cardinal', 'diagonal'] as const) {
    const bands = railPadBands(orientation)
    const width = RAIL_PAD_FAN_SPAN_DEG / padSectorCount(orientation)
    const innerRadius = (bands.dead + Math.min(bands.ring, bands.outer)) / 2
    const arc = innerRadius * width * Math.PI / 180
    assert.ok(arc >= 44, `${orientation} wedges are ${Math.round(arc)}px at the near label radius`)
  }
})

test('a cardinal pad reads left, up and right, and never a fourth', () => {
  const bands = railPadBands('cardinal')
  const resolve = (radius: number, degrees: number) => {
    const point = at(radius, degrees)
    return railPadResolve(point.dx, point.dy, 'cardinal', bands)
  }
  assert.equal(resolve(60, 0), 'right')
  assert.equal(resolve(60, 90), 'up')
  assert.equal(resolve(60, 180), 'left')
  // Everything downward is the centre, which is what makes "pull down" a cancel that always
  // has room - the one gesture a rail on the screen's bottom edge can always complete.
  assert.equal(resolve(60, -90), null)
  assert.equal(resolve(200, -90), null)
  assert.deepEqual([...padDirections('cardinal')], ['right', 'up', 'left'])
})

test('a diagonal pad reads two wedges over two rings, all of them upward', () => {
  const bands = railPadBands('diagonal')
  const resolve = (radius: number, degrees: number) => {
    const point = at(radius, degrees)
    return railPadResolve(point.dx, point.dy, 'diagonal', bands)
  }
  assert.equal(resolve(60, 35), 'upRight')
  assert.equal(resolve(60, 145), 'upLeft')
  assert.equal(resolve(150, 35), 'upRightFar')
  assert.equal(resolve(150, 145), 'upLeftFar')
  // Past the drawn edge is still the far ring: a long drag is not an abort.
  assert.equal(resolve(400, 145), 'upLeftFar')
  assert.equal(resolve(60, -90), null)
  assert.deepEqual([...padDirections('diagonal')], ['upRight', 'upLeft', 'upRightFar', 'upLeftFar'])
  assert.deepEqual(
    padDirections('diagonal').map(padRingOf),
    ['near', 'near', 'far', 'far'],
  )
})

test('the ring boundary is where the bands say it is', () => {
  const bands = railPadBands('diagonal')
  assert.equal(bands.dead, RAIL_PAD_DEAD_RADIUS_PX)
  assert.equal(bands.ring, RAIL_PAD_RING_PX)
  assert.equal(bands.outer, RAIL_PAD_OUTER_PX)
  const resolve = (radius: number) => {
    const point = at(radius, 145)
    return railPadResolve(point.dx, point.dy, 'diagonal', bands)
  }
  // Either side of the boundary rather than exactly on it: the point is built from a cosine
  // and a sine, so an exact-radius assertion would be testing floating point.
  assert.equal(resolve(RAIL_PAD_RING_PX - 2), 'upLeft')
  assert.equal(resolve(RAIL_PAD_RING_PX + 2), 'upLeftFar')
  // A one-ring pad has no boundary to cross, however far the drag goes.
  const single = railPadBands('cardinal')
  assert.equal(single.ring, Infinity)
  assert.equal(single.outer, RAIL_PAD_SINGLE_OUTER_PX)
})

test('a short pane squeezes the dial, and the gesture squeezes with it', () => {
  // The mirror of the downward problem this design removed: the fan opens upward, so upward
  // is now the direction that can run out, and a boundary you cannot travel to is a slot
  // that does not exist.
  assert.equal(railPadScaleFor('diagonal', Infinity), 1)
  assert.equal(railPadScaleFor('diagonal', RAIL_PAD_OUTER_PX * 2), 1)
  assert.equal(railPadScaleFor('diagonal', RAIL_PAD_OUTER_PX / 2), 0.5)
  assert.equal(railPadScaleFor('diagonal', 0), RAIL_PAD_MIN_SCALE)
  assert.equal(railPadScaleFor('cardinal', RAIL_PAD_SINGLE_OUTER_PX / 2), 0.5)
  const squeezed = railPadBands('diagonal', 0.5)
  assert.equal(squeezed.ring, RAIL_PAD_RING_PX / 2)
  assert.equal(squeezed.outer, RAIL_PAD_OUTER_PX / 2)
  // The dead radius is not scaled: it is already small, and shrinking it would make the pad
  // fire on a press that never really moved.
  assert.equal(squeezed.dead, RAIL_PAD_DEAD_RADIUS_PX)
})

test('a latched direction costs the switch margin to leave, across a wedge or a ring', () => {
  const bands = railPadBands('cardinal')
  const resolve = (radius: number, degrees: number, current: 'up' | 'right' | 'left' | null) => {
    const point = at(radius, degrees)
    return railPadResolve(point.dx, point.dy, 'cardinal', bands, current)
  }
  const boundary = railPadWedgeBounds('cardinal', 0).to
  // Just past the boundary keeps the latch; well past it switches.
  assert.equal(resolve(60, boundary + 2, 'right'), 'right')
  assert.equal(resolve(60, boundary + 20, 'right'), 'up')
  // With nothing latched the same point reads as its raw wedge.
  assert.equal(resolve(60, boundary + 2, null), 'up')

  const rings = railPadBands('diagonal')
  const radial = (radius: number, current: 'upLeft' | 'upLeftFar' | null) => {
    const point = at(radius, 145)
    return railPadResolve(point.dx, point.dy, 'diagonal', rings, current)
  }
  assert.equal(radial(RAIL_PAD_RING_PX + 2, 'upLeft'), 'upLeft', 'crossing outward costs the margin')
  assert.equal(radial(RAIL_PAD_RING_PX + RAIL_PAD_SWITCH_MARGIN_PX + 4, 'upLeft'), 'upLeftFar')
  assert.equal(radial(RAIL_PAD_RING_PX - 2, 'upLeftFar'), 'upLeftFar', 'and so does crossing back')
  assert.equal(radial(RAIL_PAD_RING_PX - RAIL_PAD_SWITCH_MARGIN_PX - 4, 'upLeftFar'), 'upLeft')
})

test('the label radius sits inside its own band, on both rings', () => {
  // What the dial draws has to be inside what the gesture resolves, or the text would sit in
  // a band other than the one it names.
  for (const orientation of ['cardinal', 'diagonal'] as const) {
    const bands = railPadBands(orientation)
    const near = bands.dead + (Math.min(bands.ring, bands.outer) - bands.dead) * 0.55
    assert.ok(near > bands.dead && near < Math.min(bands.ring, bands.outer))
    if (!Number.isFinite(bands.ring)) continue
    const far = bands.ring + (bands.outer - bands.ring) * 0.55
    assert.ok(far > bands.ring && far < bands.outer)
  }
})

test('a wedge centre is the middle of its own bounds', () => {
  for (const orientation of ['cardinal', 'diagonal'] as const) {
    for (let index = 0; index < padSectorCount(orientation); index += 1) {
      const { from, to } = railPadWedgeBounds(orientation, index)
      const centre = railPadWedgeCentre(orientation, index)
      assert.equal(centre, (from + to) / 2)
      assert.equal(railPadWedgeIndex(orientation, centre), index)
    }
  }
})

test('railPadAxes reports only the axes the bound wedges span', () => {
  assert.deepEqual(railPadAxes(['up']), { horizontal: false, vertical: true })
  assert.deepEqual(railPadAxes(['left', 'right']), { horizontal: true, vertical: true })
  assert.deepEqual(railPadAxes(['upLeft']), { horizontal: true, vertical: true })
  assert.deepEqual(railPadAxes([]), { horizontal: false, vertical: false })
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
  assert.deepEqual(fired, ['up'])
  advance(0)
  assert.deepEqual(fired, ['up'])
  gesture.cancel()
})

test('the dial delay is not a gesture delay', () => {
  // The one number that governs drawing, asserted to be nothing the gesture reads.
  assert.ok(RAIL_PAD_DIAL_DELAY_MS > 0)
  const { gesture, fired, press } = harness()
  press(0, 0)
  gesture.move(1, 0, -60)
  assert.deepEqual(fired, ['up'])
  gesture.cancel()
})

test('a hold repeats at the shared cadence, and only an enter-repeat slot does', () => {
  const { gesture, fired, advance, press } = harness()
  press(0, 0)
  gesture.move(1, 0, -60)
  assert.deepEqual(fired, ['up'])
  advance(RAIL_PAD_REPEAT_DELAY_MS - 1)
  assert.deepEqual(fired, ['up'])
  advance(1)
  assert.deepEqual(fired, ['up', 'up'])
  advance(RAIL_PAD_REPEAT_INTERVAL_MS * 3)
  assert.equal(fired.length, 5)
  // Switching to a plain `enter` slot fires once and stops repeating.
  gesture.move(1, 60, 0)
  const afterSwitch = fired.length
  assert.equal(fired[afterSwitch - 1], 'right')
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
  assert.deepEqual(fired, ['up', 'up', 'up'])
  gesture.cancel()
})

test('pulling down into the abort zone releases the latch and fires nothing', () => {
  // The escape that always has room, which is the reason the fan gave up its lower half.
  const { gesture, fired, latches, press } = harness()
  press(0, 0)
  gesture.move(1, 0, -60)
  assert.deepEqual(fired, ['up'])
  gesture.move(1, 0, 120)
  assert.equal(latches.at(-1)?.slot, null)
  gesture.release(1)
  assert.deepEqual(fired, ['up'], 'and the centre does not sneak in on the way out')
})

test('hysteresis is asymmetric: leaving costs less travel than entering', () => {
  const { gesture, fired, press } = harness()
  press(0, 0)
  gesture.move(1, 0, -RAIL_PAD_DEAD_RADIUS_PX)
  assert.deepEqual(fired, ['up'])
  gesture.move(1, 0, -(RAIL_PAD_DEAD_RADIUS_PX - 1))
  gesture.move(1, 0, -RAIL_PAD_DEAD_RADIUS_PX)
  assert.deepEqual(fired, ['up'])
  gesture.cancel()
})

test('a release slot waits for the lift, and dragging back out cancels it', () => {
  const held = harness()
  held.press(0, 0)
  held.gesture.move(1, -60, 0)
  assert.deepEqual(held.fired, [], 'a release slot fires nothing on the way in')
  assert.equal(held.latches.at(-1)?.armed, true)
  held.gesture.release(1)
  assert.deepEqual(held.fired, ['left'])

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
  assert.deepEqual(swapped.fired, ['right'])
})

test('the press announces its end however it finished, so the dial can be torn down', () => {
  // The chip cannot do this for itself: by the time a real gesture ends the finger is well
  // off it, and the `pointerup` belongs to whatever is underneath.
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
  const { gesture, fired, press } = harness({ slots: { ...CARDINAL_SLOTS, center: { mode: 'enter' } } })
  press(0, 0)
  gesture.move(1, -60, 0)
  gesture.move(1, 0, 0)
  gesture.release(1)
  assert.deepEqual(fired, [])
})

test('a press that never travels fires the centre, and one that did does not', () => {
  const tapped = harness({ slots: { ...CARDINAL_SLOTS, center: { mode: 'enter' } } })
  tapped.press(0, 0)
  tapped.gesture.move(1, 2, 2)
  tapped.gesture.release(1)
  assert.deepEqual(tapped.fired, ['center'])
  assert.equal(tapped.gesture.consumeHandledClick(), true, 'the centre it fired is the tap, so the click is spent')

  const dragged = harness({ slots: { ...CARDINAL_SLOTS, center: { mode: 'enter' } } })
  dragged.press(0, 0)
  dragged.gesture.move(1, 60, 0)
  dragged.gesture.release(1)
  assert.deepEqual(dragged.fired, ['right'], 'a direction was chosen, so the centre is not also run')
})

test('an unbound wedge latches, fires nothing, and blocks the centre', () => {
  const { gesture, fired, press, latches } = harness({
    slots: { up: { mode: 'enter' }, left: { mode: 'enter' }, center: { mode: 'enter' } },
  })
  press(0, 0)
  gesture.move(1, 60, 0)
  assert.deepEqual(fired, [])
  assert.equal(latches.at(-1)?.slot, 'right')
  gesture.release(1)
  assert.deepEqual(fired, [], 'releasing into a dead wedge is a deliberate abort, not a centre tap')
})

test('a disabled slot is a dead wedge, not a hidden one', () => {
  const { gesture, fired, press, latches } = harness({
    slots: { up: { mode: 'enter', disabled: true }, right: { mode: 'enter' } },
  })
  press(0, 0)
  gesture.move(1, 0, -60)
  assert.equal(latches.at(-1)?.slot, 'up', 'the direction is still where it was')
  assert.deepEqual(fired, [])
  gesture.move(1, 60, 0)
  assert.deepEqual(fired, ['right'])
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
  const { gesture, fired, press } = harness({ slots: { up: { mode: 'enter' } } })
  press(0, 0)
  assert.equal(pointerDragOwnsPointer(), false, 'nothing claimed yet: this pad has an axis to give away')
  gesture.move(1, RAIL_PAN_SLOP_PX, 0)
  assert.equal(pointerDragOwnsPointer(), false)
  // The press is over for the pad: coming back vertically must not steal a pan already begun.
  gesture.move(1, 0, -60)
  assert.deepEqual(fired, [])
})

test('a vertical-only pad still claims once the drag goes its way', () => {
  const { gesture, fired, press } = harness({ slots: { up: { mode: 'enter' } } })
  press(0, 0)
  gesture.move(1, 0, -RAIL_PAN_SLOP_PX)
  assert.equal(pointerDragOwnsPointer(), true)
  gesture.move(1, 0, -60)
  assert.deepEqual(fired, ['up'])
  gesture.release(1)
  assert.equal(pointerDragOwnsPointer(), false)
})

test('only the pointer that opened a press may move or end it', () => {
  const { gesture, fired, press } = harness()
  press(0, 0)
  assert.equal(gesture.move(2, 0, -60), false)
  assert.deepEqual(fired, [])
  assert.equal(gesture.release(2), false)
  assert.equal(gesture.press(2, 0, 0, { orientation: 'cardinal', slots: CARDINAL_SLOTS }), false)
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
  const { gesture, press } = harness({ orientation: 'diagonal', slots: { upLeft: { mode: 'enter' }, upLeftFar: { mode: 'enter' } }, roomAbovePx: RAIL_PAD_OUTER_PX / 2 })
  press(0, 0)
  const { bands } = gesture.peek()
  assert.equal(bands.ring, RAIL_PAD_RING_PX / 2)
  assert.equal(bands.outer, RAIL_PAD_OUTER_PX / 2)
  gesture.cancel()
})
