import assert from 'node:assert/strict'
import test from 'node:test'
import { hasLayoutBox, whenLayoutBox, type SizeObserverLike } from '../src/layoutBox.ts'

/** An element whose box can be taken away and given back, the way `display:none` does. */
function fakeElement(boxed: boolean) {
  const state = { boxed }
  const element = {
    getClientRects: () => (state.boxed ? [{ width: 100, height: 40 }] : []),
  } as unknown as Element
  return { element, state }
}

function fakeObserver() {
  const observed: Element[] = []
  let notify: (() => void) | null = null
  let disconnects = 0
  const create = (onResize: () => void): SizeObserverLike => {
    notify = onResize
    return {
      observe: target => observed.push(target),
      disconnect: () => { disconnects += 1 },
    }
  }
  return { create, observed, fire: () => notify?.(), disconnectCount: () => disconnects }
}

test('an element inside a display:none subtree reports no layout box', () => {
  assert.equal(hasLayoutBox(fakeElement(true).element), true)
  assert.equal(hasLayoutBox(fakeElement(false).element), false)
})

test('an element that already has a box reports immediately, without observing', () => {
  const { element } = fakeElement(true)
  const observer = fakeObserver()
  let calls = 0
  whenLayoutBox(element, () => { calls += 1 }, observer.create)

  assert.equal(calls, 1)
  assert.deepEqual(observer.observed, [])
})

test('a hidden element waits, then reports once it gains a box', () => {
  const { element, state } = fakeElement(false)
  const observer = fakeObserver()
  let calls = 0
  whenLayoutBox(element, () => { calls += 1 }, observer.create)

  assert.equal(calls, 0)
  assert.deepEqual(observer.observed, [element])

  // A resize that leaves it boxless (a sibling reflow) is not the transition being waited for.
  observer.fire()
  assert.equal(calls, 0)

  state.boxed = true
  observer.fire()
  assert.equal(calls, 1)
  assert.equal(observer.disconnectCount(), 1)

  // At most once: later resizes of the now-visible element are the caller's business.
  observer.fire()
  assert.equal(calls, 1)
})

test('cancelling before the reveal keeps the callback from running', () => {
  const { element, state } = fakeElement(false)
  const observer = fakeObserver()
  let calls = 0
  const cancel = whenLayoutBox(element, () => { calls += 1 }, observer.create)

  cancel()
  state.boxed = true
  observer.fire()

  assert.equal(calls, 0)
  assert.equal(observer.disconnectCount(), 1)
})

test('a host with no ResizeObserver reports now rather than never', () => {
  const { element } = fakeElement(false)
  let calls = 0
  whenLayoutBox(element, () => { calls += 1 }, () => null)

  assert.equal(calls, 1)
})
