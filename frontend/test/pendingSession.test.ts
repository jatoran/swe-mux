import assert from 'node:assert/strict'
import test from 'node:test'
import {
  activateContainingStack,
  emptyLayout,
  openTab,
  stackForView,
  terminalLeaf,
} from '../src/layout.ts'
import { placePendingTerminal, selectPendingTerminal } from '../src/pendingSession.ts'

const placement = {
  split: false as const,
  targetId: 'first',
  position: 'after' as const,
}

function twoTabs() {
  const first = openTab(emptyLayout(), null, terminalLeaf('first'))
  return openTab(first, 'first', terminalLeaf('second'))
}

test('a new pending session receives initial focus', () => {
  const placed = placePendingTerminal(twoTabs(), 'pending-1', placement)
  assert.equal(stackForView(placed, 'pending-1')?.active_child_id, 'pending-1')
})

test('fleet reconciliation restores a pending tab without stealing newer focus', () => {
  const placed = placePendingTerminal(twoTabs(), 'pending-1', placement, false)
  assert.equal(stackForView(placed, 'pending-1')?.active_child_id, 'second')
})

test('selecting an unpanned pending session leaves every split and tab unchanged', () => {
  const layout = twoTabs()
  assert.equal(selectPendingTerminal(layout, 'pending-1'), layout)
})

test('selecting an ordinary pending terminal activates its existing pane tab', () => {
  const withPending = placePendingTerminal(twoTabs(), 'pending-1', placement)
  const movedAway = activateContainingStack(withPending, 'second')
  assert.equal(
    stackForView(selectPendingTerminal(movedAway, 'pending-1'), 'pending-1')?.active_child_id,
    'pending-1',
  )
})
