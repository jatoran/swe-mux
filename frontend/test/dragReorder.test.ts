import assert from 'node:assert/strict'
import test from 'node:test'
import {
  edgeAutoScrollDelta, MOBILE_PROJECT_HOLD_DRAG, POINTER_MOVE_DRAG, pointerDragMoveDecision,
  reorderForHover, reorderTargetForPoint,
} from '../src/dragReorder.ts'

test('movement drag activates only after its threshold', () => {
  assert.equal(pointerDragMoveDecision(POINTER_MOVE_DRAG, 4.99), 'wait')
  assert.equal(pointerDragMoveDecision(POINTER_MOVE_DRAG, 5), 'activate')
})

test('mobile Project hold tolerates jitter but yields to scrolling movement', () => {
  assert.equal(MOBILE_PROJECT_HOLD_DRAG.delayMs, 325)
  assert.equal(pointerDragMoveDecision(MOBILE_PROJECT_HOLD_DRAG, 8), 'wait')
  assert.equal(pointerDragMoveDecision(MOBILE_PROJECT_HOLD_DRAG, 8.01), 'cancel')
})

test('edge auto-scroll accelerates toward either edge and stops in the middle', () => {
  assert.equal(edgeAutoScrollDelta(100, 0, 200), 0)
  assert.equal(edgeAutoScrollDelta(0, 0, 200), -18)
  assert.equal(edgeAutoScrollDelta(200, 0, 200), 18)
  assert.ok(edgeAutoScrollDelta(20, 0, 200)<0)
  assert.ok(edgeAutoScrollDelta(180, 0, 200)>0)
  assert.equal(edgeAutoScrollDelta(0, 10, 10), 0)
})

test('hover reorder removes the dragged id before placing it', () => {
  assert.deepEqual(reorderForHover(['a','b','c'],'a','c','after'),['b','c','a'])
  assert.deepEqual(reorderForHover(['a','b','c'],'c','a','before'),['c','a','b'])
})

test('vertical target chooses the nearest insertion side around dragged rows', () => {
  const rows=[
    {id:'a',start:0,end:20},
    {id:'b',start:20,end:40},
    {id:'c',start:40,end:60},
  ]
  assert.deepEqual(reorderTargetForPoint(rows,'b',5),{id:'a',side:'before'})
  assert.deepEqual(reorderTargetForPoint(rows,'b',35),{id:'c',side:'before'})
  assert.deepEqual(reorderTargetForPoint(rows,'b',70),{id:'c',side:'after'})
})
