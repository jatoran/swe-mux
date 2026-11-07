import assert from 'node:assert/strict'
import { afterEach, beforeEach, describe, mock, test } from 'node:test'
import { guardHeldPointer } from '../src/holdMenuGuard.ts'

// The bug these pin: long-press a Project on a phone and the menu opens under the finger, so
// lifting that finger pressed whichever row it had landed on - usually Rename.

const HELD = 7

function dispatch(target: EventTarget, type: string, fields: Record<string, unknown> = {}) {
  const event = new Event(type, { bubbles: true, cancelable: true })
  Object.assign(event, fields)
  target.dispatchEvent(event)
  return event
}

/** Whether a tap's click would go on from the window's capture phase to the row under it.
 *  A bare EventTarget has no tree to stop propagating through, so this reads the verdict. */
function tapReachesMenu(target: EventTarget, fields: Record<string, unknown> = { detail: 1 }) {
  const click = dispatch(target, 'click', fields)
  return !click.cancelBubble && !click.defaultPrevented
}

describe('a menu opened by a touch hold', () => {
  // Inside the describe: all.ts runs every test file in one process, so a top-level hook
  // would mock setTimeout for the whole suite.
  beforeEach(() => mock.timers.enable({ apis: ['setTimeout'] }))
  afterEach(() => mock.timers.reset())

  test('the click from lifting the held finger never reaches the menu', () => {
    const target = new EventTarget()
    const end = guardHeldPointer(HELD, target)

    dispatch(target, 'pointerup', { pointerId: HELD })

    assert.equal(tapReachesMenu(target), false)
    end()
  })

  test('the guard does not depend on how long the finger stayed down', () => {
    const target = new EventTarget()
    const end = guardHeldPointer(HELD, target)

    // A window measured from the menu opening would have closed by now.
    mock.timers.tick(60_000)
    dispatch(target, 'pointerup', { pointerId: HELD })

    assert.equal(tapReachesMenu(target), false)
    end()
  })

  test('a deliberate tap right after the lift works', () => {
    const target = new EventTarget()
    guardHeldPointer(HELD, target)

    dispatch(target, 'pointerup', { pointerId: HELD })
    mock.timers.tick(10)
    dispatch(target, 'pointerdown', { pointerId: HELD + 1 })
    dispatch(target, 'pointerup', { pointerId: HELD + 1 })

    assert.equal(tapReachesMenu(target), true)
  })

  test('it swallows only the one click the lift produces', () => {
    const target = new EventTarget()
    guardHeldPointer(HELD, target)

    dispatch(target, 'pointerup', { pointerId: HELD })

    assert.equal(tapReachesMenu(target), false)
    assert.equal(tapReachesMenu(target), true)
  })

  test('it ends by itself when the lift produced no click', () => {
    const target = new EventTarget()
    guardHeldPointer(HELD, target)

    dispatch(target, 'pointerup', { pointerId: HELD })
    mock.timers.tick(5_000)

    assert.equal(tapReachesMenu(target), true)
  })

  test('a cancelled pointer ends it the same way as a lift', () => {
    const target = new EventTarget()
    guardHeldPointer(HELD, target)

    dispatch(target, 'pointercancel', { pointerId: HELD })
    mock.timers.tick(5_000)

    assert.equal(tapReachesMenu(target), true)
  })

  test("another finger's lift does not start the end of it", () => {
    const target = new EventTarget()
    const end = guardHeldPointer(HELD, target)

    dispatch(target, 'pointerup', { pointerId: HELD + 1 })
    mock.timers.tick(5_000)

    assert.equal(tapReachesMenu(target), false)
    end()
  })

  test('keyboard and scripted activation pass straight through', () => {
    const target = new EventTarget()
    const end = guardHeldPointer(HELD, target)

    assert.equal(tapReachesMenu(target, { detail: 0 }), true)
    end()
  })

  test('a new hold replaces the previous guard rather than stacking on it', () => {
    const target = new EventTarget()
    guardHeldPointer(HELD, target)
    const end = guardHeldPointer(HELD + 1, target)

    // Still armed for the new pointer. The old pointer's lift means nothing now.
    dispatch(target, 'pointerup', { pointerId: HELD })
    mock.timers.tick(5_000)
    assert.equal(tapReachesMenu(target), false)

    end()
    assert.equal(tapReachesMenu(target), true)
  })
})
