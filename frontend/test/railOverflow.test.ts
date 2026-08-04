import assert from 'node:assert/strict'
import test from 'node:test'
import { railFocusTarget, railOverflowState, railPageTarget } from '../src/railOverflow.ts'

test('rail edge controls appear only in directions with hidden content', () => {
  assert.deepEqual(railOverflowState({ scrollLeft: 0, scrollWidth: 300, clientWidth: 300 }), { left: false, right: false })
  assert.deepEqual(railOverflowState({ scrollLeft: 0, scrollWidth: 900, clientWidth: 300 }), { left: false, right: true })
  assert.deepEqual(railOverflowState({ scrollLeft: 240, scrollWidth: 900, clientWidth: 300 }), { left: true, right: true })
  assert.deepEqual(railOverflowState({ scrollLeft: 600, scrollWidth: 900, clientWidth: 300 }), { left: true, right: false })
})

test('rail edge tolerance prevents controls flickering at endpoints', () => {
  assert.deepEqual(railOverflowState({ scrollLeft: 0.75, scrollWidth: 900, clientWidth: 300 }), { left: false, right: true })
  assert.deepEqual(railOverflowState({ scrollLeft: 599.5, scrollWidth: 900, clientWidth: 300 }), { left: true, right: false })
  assert.deepEqual(railOverflowState({ scrollLeft: -8, scrollWidth: 900, clientWidth: 300 }), { left: false, right: true })
})

test('rail paging preserves context and settles on item boundaries', () => {
  const offsets = [0, 60, 120, 180, 240, 300, 360, 420, 480, 540, 600]
  assert.equal(railPageTarget({ scrollLeft: 0, scrollWidth: 900, clientWidth: 300 }, offsets, 1), 300)
  assert.equal(railPageTarget({ scrollLeft: 300, scrollWidth: 900, clientWidth: 300 }, offsets, 1), 600)
  assert.equal(railPageTarget({ scrollLeft: 600, scrollWidth: 900, clientWidth: 300 }, offsets, -1), 300)
  assert.equal(railPageTarget({ scrollLeft: 300, scrollWidth: 900, clientWidth: 300 }, offsets, -1), 0)
})

test('rail paging settles on uneven tab boundaries', () => {
  const offsets = [0, 90, 235, 410, 585, 760]
  assert.equal(railPageTarget({ scrollLeft: 0, scrollWidth: 940, clientWidth: 300 }, offsets, 1), 410)
  assert.equal(railPageTarget({ scrollLeft: 410, scrollWidth: 940, clientWidth: 300 }, offsets, -1), 90)
})

test('focused items are moved clear of both overlay controls', () => {
  const metrics = { scrollLeft: 200, scrollWidth: 900, clientWidth: 300 }
  assert.equal(railFocusTarget(metrics, 210, 260), 182)
  assert.equal(railFocusTarget(metrics, 460, 520), 248)
  assert.equal(railFocusTarget(metrics, 300, 360), 200)
})
