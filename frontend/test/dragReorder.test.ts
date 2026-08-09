import assert from 'node:assert/strict'
import test from 'node:test'
import {
  edgeAutoScrollDelta, insertionEdge, listDropTargetForPoint, MOBILE_PROJECT_HOLD_DRAG,
  POINTER_MOVE_DRAG, pointerDragMoveDecision, reorderForHover, reorderTargetForPoint,
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

test('an insertion edge is a fraction of the row, floored and capped', () => {
  // A phone row and a dense desktop row must both keep a landing strip that can be aimed at,
  // and neither may swallow the middle band that means "group with this one".
  assert.equal(insertionEdge(22),6.6)
  assert.equal(insertionEdge(10),5)
  assert.equal(insertionEdge(80),12)
  assert.equal(insertionEdge(0),5)
})

test('a row drop reads as insertion at its edges and as grouping through its middle', () => {
  const rows=[
    {id:'a',start:0,end:40},
    {id:'b',start:40,end:80},
    {id:'c',start:80,end:120},
  ]
  assert.deepEqual(listDropTargetForPoint(rows,'a',82),{kind:'insert',id:'c',side:'before'})
  assert.deepEqual(listDropTargetForPoint(rows,'a',118),{kind:'insert',id:'c',side:'after'})
  assert.deepEqual(listDropTargetForPoint(rows,'a',100),{kind:'group',id:'c'})
})

test('the dragged row is not its own target, so the seam it leaves behind is one slot', () => {
  const rows=[
    {id:'a',start:0,end:40},
    {id:'b',start:40,end:80},
    {id:'c',start:80,end:120},
  ]
  // Over its own middle: nothing to group with there, and the nearest gap is where it already is.
  assert.deepEqual(listDropTargetForPoint(rows,'b',60),{kind:'insert',id:'c',side:'before'})
  assert.equal(listDropTargetForPoint([{id:'a',start:0,end:40}],'a',20),null)
})

test('a row that cannot be grouped with is an insertion target over its whole height', () => {
  const rows=[{id:'a',start:0,end:40},{id:'b',start:40,end:80}]
  const groupable=(id:string)=>id!=='b'
  assert.deepEqual(listDropTargetForPoint(rows,'a',55,groupable),{kind:'insert',id:'b',side:'before'})
  assert.deepEqual(listDropTargetForPoint(rows,'a',65,groupable),{kind:'insert',id:'b',side:'after'})
  // The predicate only speaks for the row it rejects.
  assert.deepEqual(listDropTargetForPoint(rows,'b',20,groupable),{kind:'group',id:'a'})
})

test('gaps and the space past either end of a list resolve to the nearest slot, never a group', () => {
  const rows=[{id:'a',start:0,end:40},{id:'b',start:60,end:100}]
  assert.deepEqual(listDropTargetForPoint(rows,'x',-30),{kind:'insert',id:'a',side:'before'})
  assert.deepEqual(listDropTargetForPoint(rows,'x',50),{kind:'insert',id:'b',side:'before'})
  assert.deepEqual(listDropTargetForPoint(rows,'x',400),{kind:'insert',id:'b',side:'after'})
  assert.equal(listDropTargetForPoint([],'x',10),null)
})
