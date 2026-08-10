import assert from 'node:assert/strict'
import test from 'node:test'
import {
  activateContainingStack,
  emptyLayout,
  openTab,
  stackForView,
  terminalLeaf,
} from '../src/layout.ts'
import { placePendingTerminal, replacePendingTerminal } from '../src/pendingSession.ts'

const placement = {
  projectId: 'project-1',
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

test('resolution follows the pending tab only while the user is still on it', () => {
  const focused = placePendingTerminal(twoTabs(), 'pending-1', placement)
  const resolved = replacePendingTerminal(focused, 'pending-1', 'session-1')
  assert.equal(stackForView(resolved, 'session-1')?.active_child_id, 'session-1')

  const movedAway = activateContainingStack(focused, 'second')
  const backgroundResolution = replacePendingTerminal(movedAway, 'pending-1', 'session-1')
  assert.equal(stackForView(backgroundResolution, 'session-1')?.active_child_id, 'second')
})
