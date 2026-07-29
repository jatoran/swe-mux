import assert from 'node:assert/strict'
import test from 'node:test'
import { claimPointerDrag, markPointerDragClaims, pointerDragOwnsPointer } from '../src/pointerDragClaim.ts'

test('a claim owns the pointer until it is released', () => {
  assert.equal(pointerDragOwnsPointer(), false)
  const release = claimPointerDrag()
  assert.equal(pointerDragOwnsPointer(), true)
  release()
  assert.equal(pointerDragOwnsPointer(), false)
})

test('releasing twice cannot hand the pointer back from under another drag', () => {
  const first = claimPointerDrag()
  first()
  first()
  const second = claimPointerDrag()
  assert.equal(pointerDragOwnsPointer(), true)
  second()
  assert.equal(pointerDragOwnsPointer(), false)
})

test('overlapping claims hold the pointer until the last one releases', () => {
  const outer = claimPointerDrag()
  const inner = claimPointerDrag()
  inner()
  assert.equal(pointerDragOwnsPointer(), true)
  outer()
  assert.equal(pointerDragOwnsPointer(), false)
})

test('a drag that began and ended inside a touch sequence still owns it', () => {
  // The order that makes this necessary: touchstart marks, the drag activates on a
  // pointermove, and its pointerup releases the claim *before* touchend asks. Reading a
  // live boolean at touchend would say "no drag" and classify the drag as a swipe.
  const mark = markPointerDragClaims()
  claimPointerDrag()()
  assert.equal(pointerDragOwnsPointer(mark), true)
  // A sequence starting after that drag is unaffected: gestures work again immediately.
  assert.equal(pointerDragOwnsPointer(markPointerDragClaims()), false)
})

test('a touch sequence with no drag in it is left alone', () => {
  const mark = markPointerDragClaims()
  assert.equal(pointerDragOwnsPointer(mark), false)
})
