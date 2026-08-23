import assert from 'node:assert/strict'
import test from 'node:test'

import {
  createRailPadGesture,
  RAIL_PAD_CLEARANCE_RATIO,
  RAIL_PAD_ENTER_RADIUS_PX,
  RAIL_PAD_EXIT_RATIO,
  RAIL_PAD_MIN_RADIUS_PX,
  RAIL_PAD_REPEAT_DELAY_MS,
  RAIL_PAD_REPEAT_INTERVAL_MS,
  RAIL_PAD_SWITCH_MARGIN_PX,
  railPadAxes,
  railPadRadius,
  railPadSector,
  type RailPadPressOptions,
  type RailPadSlotKey,
} from '../src/railPadGesture.ts'
import { markPointerDragClaims, pointerDragOwnsPointer } from '../src/pointerDragClaim.ts'
import { RAIL_PAN_SLOP_PX } from '../src/railOverflow.ts'

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
    slots: { up: { mode: 'enter-repeat' }, down: { mode: 'enter' }, left: { mode: 'release' }, right: { mode: 'enter' } },
    ...options,
  })
  return { gesture, fired, latches, ends, advance, press }
}

const CARDINAL_SLOTS: RailPadPressOptions['slots'] = {
  up: { mode: 'enter-repeat' },
  down: { mode: 'enter' },
  left: { mode: 'release' },
  right: { mode: 'enter' },
}

test('cardinal sectors split on the diagonals, diagonal sectors on the axes', () => {
  assert.equal(railPadSector(10, 3, 'cardinal'), 'right')
  assert.equal(railPadSector(-10, 3, 'cardinal'), 'left')
  assert.equal(railPadSector(3, -10, 'cardinal'), 'up')
  assert.equal(railPadSector(3, 10, 'cardinal'), 'down')
  // Exactly on the diagonal resolves horizontally, deterministically rather than by luck.
  assert.equal(railPadSector(10, 10, 'cardinal'), 'right')

  assert.equal(railPadSector(-10, -10, 'diagonal'), 'upLeft')
  assert.equal(railPadSector(10, -10, 'diagonal'), 'upRight')
  assert.equal(railPadSector(10, 10, 'diagonal'), 'downRight')
  assert.equal(railPadSector(-10, 10, 'diagonal'), 'downLeft')
  // A drag that is barely off an axis still lands in a quadrant: the diagonal carving has
  // no dead zone between its wedges, which is what makes each axis carry one binary choice.
  assert.equal(railPadSector(1, -30, 'diagonal'), 'upRight')
  assert.equal(railPadSector(-1, -30, 'diagonal'), 'upLeft')
})

test('a latched direction costs the switch margin to leave, in either orientation', () => {
  // Cardinal: past the diagonal by less than the margin keeps the current direction.
  assert.equal(railPadSector(20, 22, 'cardinal', 'right'), 'right')
  assert.equal(railPadSector(20, 20 + RAIL_PAD_SWITCH_MARGIN_PX + 1, 'cardinal', 'right'), 'down')
  // ...and with nothing latched the same point reads as its raw sector.
  assert.equal(railPadSector(20, 22, 'cardinal'), 'down')

  // Diagonal: the boundary is the axis, so the margin is measured across it.
  assert.equal(railPadSector(-2, 30, 'diagonal', 'downRight'), 'downRight')
  assert.equal(railPadSector(-(RAIL_PAD_SWITCH_MARGIN_PX + 2), 30, 'diagonal', 'downRight'), 'downLeft')
})

test('a descending direction shrinks its radius to the room below, never past the pan slop', () => {
  assert.equal(railPadRadius('up', 4), RAIL_PAD_ENTER_RADIUS_PX)
  assert.equal(railPadRadius('down'), RAIL_PAD_ENTER_RADIUS_PX)
  assert.equal(railPadRadius('down', 1000), RAIL_PAD_ENTER_RADIUS_PX)
  // Squeezed: 40% of what is left below the finger.
  assert.equal(railPadRadius('down', 20), 20 * RAIL_PAD_CLEARANCE_RATIO)
  // Floored, so a pad flush with the screen edge cannot fire on a press that never moved.
  assert.equal(railPadRadius('down', 2), RAIL_PAD_MIN_RADIUS_PX)
  assert.equal(railPadRadius('down', 0), RAIL_PAD_MIN_RADIUS_PX)
  // Both descending diagonals are squeezed; neither ascending one is.
  assert.equal(railPadRadius('downLeft', 20), 20 * RAIL_PAD_CLEARANCE_RATIO)
  assert.equal(railPadRadius('downRight', 20), 20 * RAIL_PAD_CLEARANCE_RATIO)
  assert.equal(railPadRadius('upLeft', 20), RAIL_PAD_ENTER_RADIUS_PX)
})

test('railPadAxes reports only the axes the bound directions span', () => {
  assert.deepEqual(railPadAxes(['up', 'down']), { horizontal: false, vertical: true })
  assert.deepEqual(railPadAxes(['left', 'right']), { horizontal: true, vertical: false })
  assert.deepEqual(railPadAxes(['up', 'right']), { horizontal: true, vertical: true })
  // Every diagonal spans both, which is why a diagonal pad always claims at pointer-down.
  assert.deepEqual(railPadAxes(['upLeft']), { horizontal: true, vertical: true })
  assert.deepEqual(railPadAxes([]), { horizontal: false, vertical: false })
})

test('a press fires nothing, and crossing the radius fires once immediately', () => {
  const { gesture, fired, advance, press } = harness()
  assert.equal(press(100, 100), true)
  assert.deepEqual(fired, [])
  // Short of the radius is still neutral.
  gesture.move(1, 100, 100 - (RAIL_PAD_ENTER_RADIUS_PX - 1))
  assert.deepEqual(fired, [])
  // Crossing it fires on entry, with no clock involved at all.
  gesture.move(1, 100, 100 - RAIL_PAD_ENTER_RADIUS_PX)
  assert.deepEqual(fired, ['up'])
  advance(0)
  assert.deepEqual(fired, ['up'])
  gesture.cancel()
})

test('a hold repeats at the shared cadence, and only an enter-repeat slot does', () => {
  const { gesture, fired, advance, press } = harness()
  press(0, 0)
  gesture.move(1, 0, -40)
  assert.deepEqual(fired, ['up'])
  advance(RAIL_PAD_REPEAT_DELAY_MS - 1)
  assert.deepEqual(fired, ['up'])
  advance(1)
  assert.deepEqual(fired, ['up', 'up'])
  advance(RAIL_PAD_REPEAT_INTERVAL_MS * 3)
  assert.equal(fired.length, 5)
  // Switching to a plain `enter` slot fires once and stops repeating.
  gesture.move(1, 0, 40)
  const afterSwitch = fired.length
  assert.equal(fired[afterSwitch - 1], 'down')
  advance(RAIL_PAD_REPEAT_DELAY_MS + RAIL_PAD_REPEAT_INTERVAL_MS * 5)
  assert.equal(fired.length, afterSwitch)
  gesture.cancel()
})

test('leaving and re-entering a direction fires it again', () => {
  const { gesture, fired, press } = harness()
  press(0, 0)
  gesture.move(1, 0, -40)
  // Back inside the exit radius is neutral again...
  gesture.move(1, 0, -(RAIL_PAD_ENTER_RADIUS_PX * RAIL_PAD_EXIT_RATIO) + 0.5)
  // ...so crossing out once more is a second press, with no finger lifted.
  gesture.move(1, 0, -40)
  gesture.move(1, 0, 0)
  gesture.move(1, 0, -40)
  assert.deepEqual(fired, ['up', 'up', 'up'])
  gesture.cancel()
})

test('hysteresis is asymmetric: leaving costs less travel than entering', () => {
  const { gesture, fired, press } = harness()
  press(0, 0)
  gesture.move(1, 0, -RAIL_PAD_ENTER_RADIUS_PX)
  assert.deepEqual(fired, ['up'])
  // Just inside the entry radius does *not* release the latch, so a finger resting on the
  // boundary cannot chatter the key.
  gesture.move(1, 0, -(RAIL_PAD_ENTER_RADIUS_PX - 1))
  gesture.move(1, 0, -RAIL_PAD_ENTER_RADIUS_PX)
  assert.deepEqual(fired, ['up'])
  gesture.cancel()
})

test('a release slot waits for the lift, and dragging back out cancels it', () => {
  const held = harness()
  held.press(0, 0)
  held.gesture.move(1, -40, 0)
  assert.deepEqual(held.fired, [], 'a release slot fires nothing on the way in')
  assert.equal(held.latches.at(-1)?.armed, true)
  held.gesture.release(1)
  assert.deepEqual(held.fired, ['left'])

  const escaped = harness()
  escaped.press(0, 0)
  escaped.gesture.move(1, -40, 0)
  // Out of the slot and back to neutral: the whole point of the mode.
  escaped.gesture.move(1, 0, 0)
  escaped.gesture.release(1)
  assert.deepEqual(escaped.fired, [])

  const swapped = harness()
  swapped.press(0, 0)
  swapped.gesture.move(1, -40, 0)
  // Straight from the armed slot into another direction: the armed one still never runs.
  swapped.gesture.move(1, 40, 0)
  swapped.gesture.release(1)
  assert.deepEqual(swapped.fired, ['right'])
})

test('the press announces its end however it finished, so the petals can be torn down', () => {
  // The chip cannot do this for itself: by the time a real gesture ends the finger is well
  // off it, and the `pointerup` belongs to whatever is underneath.
  const lifted = harness()
  lifted.press(0, 0)
  lifted.gesture.move(1, 0, -40)
  assert.deepEqual(lifted.ends, [])
  lifted.gesture.release(1)
  assert.equal(lifted.ends.length, 1)

  const cancelled = harness()
  cancelled.press(0, 0)
  cancelled.gesture.move(1, 0, -40)
  cancelled.gesture.cancel()
  assert.equal(cancelled.ends.length, 1)
  // Cancelling with nothing open announces nothing, so a blur cannot spam it.
  cancelled.gesture.cancel()
  assert.equal(cancelled.ends.length, 1)
})

test('returning to the centre to abort runs nothing, even with a centre bound', () => {
  // An escape hatch that ran the centre instead would be a redirect, not an escape.
  const { gesture, fired, press } = harness({ slots: { ...CARDINAL_SLOTS, center: { mode: 'enter' } } })
  press(0, 0)
  gesture.move(1, -40, 0)
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
  dragged.gesture.move(1, 0, 40)
  dragged.gesture.release(1)
  assert.deepEqual(dragged.fired, ['down'], 'a direction was chosen, so the centre is not also run')
})

test('an unbound direction latches, fires nothing, and blocks the centre', () => {
  // Both axes bound, so the pad owns this drag: the empty direction is a real target and
  // not a horizontal flick the pad has handed to the strip.
  const { gesture, fired, press, latches } = harness({
    slots: { up: { mode: 'enter' }, left: { mode: 'enter' }, center: { mode: 'enter' } },
  })
  press(0, 0)
  gesture.move(1, 40, 0)
  assert.deepEqual(fired, [])
  assert.equal(latches.at(-1)?.slot, 'right')
  gesture.release(1)
  // Releasing into a dead direction is a deliberate abort, not a centre tap.
  assert.deepEqual(fired, [])
})

test('a disabled slot is a dead direction, not a hidden one', () => {
  const { gesture, fired, press, latches } = harness({
    slots: { up: { mode: 'enter', disabled: true }, down: { mode: 'enter' } },
  })
  press(0, 0)
  gesture.move(1, 0, -40)
  assert.equal(latches.at(-1)?.slot, 'up', 'the direction is still where it was')
  assert.deepEqual(fired, [])
  gesture.move(1, 0, 40)
  assert.deepEqual(fired, ['down'])
  gesture.cancel()
})

test('a cancel fires nothing, including from an armed release slot', () => {
  const { gesture, fired, press } = harness()
  press(0, 0)
  gesture.move(1, -40, 0)
  gesture.cancel()
  assert.deepEqual(fired, [])
  // And the pointer is handed back, so the rail can pan again.
  assert.equal(pointerDragOwnsPointer(), false)
})

test('a two-axis pad claims the pointer at press, so the pan and the menu swipe stand down', () => {
  const mark = markPointerDragClaims()
  const { gesture, press } = harness()
  press(0, 0)
  assert.equal(pointerDragOwnsPointer(), true)
  gesture.release(1)
  assert.equal(pointerDragOwnsPointer(), false)
  // The generation mark is what the recognizer reads at `touchend`, which arrives after
  // the `pointerup` that released the claim - a live boolean there would always say "no".
  assert.equal(pointerDragOwnsPointer(mark), true)
})

test('a one-axis pad yields the other axis to the rail pan', () => {
  const { gesture, fired, press } = harness({ slots: { up: { mode: 'enter' }, down: { mode: 'enter' } } })
  press(0, 0)
  assert.equal(pointerDragOwnsPointer(), false, 'nothing claimed yet: this pad has an axis to give away')
  // A horizontal flick belongs to the strip, decided at the pan's own slop so the two can
  // never both take it.
  gesture.move(1, RAIL_PAN_SLOP_PX, 0)
  assert.equal(pointerDragOwnsPointer(), false)
  // The press is over for the pad: coming back vertically must not steal a pan already begun.
  gesture.move(1, 0, -40)
  assert.deepEqual(fired, [])
})

test('a one-axis pad still claims once the drag goes its way', () => {
  const { gesture, fired, press } = harness({ slots: { up: { mode: 'enter' }, down: { mode: 'enter' } } })
  press(0, 0)
  gesture.move(1, 0, -RAIL_PAN_SLOP_PX)
  assert.equal(pointerDragOwnsPointer(), true)
  gesture.move(1, 0, -40)
  assert.deepEqual(fired, ['up'])
  gesture.release(1)
  assert.equal(pointerDragOwnsPointer(), false)
})

test('only the pointer that opened a press may move or end it', () => {
  const { gesture, fired, press } = harness()
  press(0, 0)
  assert.equal(gesture.move(2, 0, -40), false)
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
  gesture.move(1, 0, -40)
  gesture.release(1)
  assert.equal(gesture.consumeHandledClick(), true)
  assert.equal(gesture.consumeHandledClick(), false, 'one-shot')

  // A hold whose click never arrived must not swallow the next press's tap instead.
  press(0, 0)
  gesture.move(1, 0, -40)
  gesture.release(1)
  press(0, 0)
  assert.equal(gesture.consumeHandledClick(), false)
  gesture.cancel()
})

test('a squeezed downward slot commits within the room it actually has', () => {
  const { gesture, fired, press } = harness({ clearanceBelowPx: 18 })
  press(0, 0)
  // 40% of 18 is 7.2px, well inside the 10px a roomy pad would ask for - and inside the
  // travel Android's bottom-edge gesture needs before it recognises.
  gesture.move(1, 0, 7.2)
  assert.deepEqual(fired, ['down'])
  gesture.cancel()
})

test('the peek radii are the ones the gesture is actually using, so the petals cannot lie', () => {
  const { gesture, press } = harness({ clearanceBelowPx: 18 })
  press(0, 0)
  const { radius } = gesture.peek()
  assert.equal(radius('down'), 18 * RAIL_PAD_CLEARANCE_RATIO)
  assert.equal(radius('up'), RAIL_PAD_ENTER_RADIUS_PX)
  gesture.cancel()
})
