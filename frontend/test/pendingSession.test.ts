import assert from 'node:assert/strict'
import test from 'node:test'
import {
  activateContainingStack,
  emptyLayout,
  openTab,
  stackForView,
  terminalIds,
  terminalLeaf,
} from '../src/layout.ts'
import { mobileWorkspaceProjection } from '../src/mobileWorkspace.ts'
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

test('a resume that places its pane first is the pane a phone actually shows', () => {
  // The History resume used to await the daemon before touching anything, and proving a
  // resumed pane came up takes seconds by design. Focus therefore named a session no
  // layout held yet, and the mobile projection - which has no equivalent of the desktop
  // reconciler's outstanding request - fell through to whatever tab the pane was already
  // showing. The workspace stayed where it was and jumped when the refresh arrived, which
  // is the "it didn't navigate me there" half of the report.
  const layout = twoTabs()
  assert.equal(mobileWorkspaceProjection(layout, 'resumed', 'resumed').selected?.id, 'second')
  const placed = placePendingTerminal(layout, 'pending-1', placement)
  assert.equal(
    mobileWorkspaceProjection(placed, 'pending-1', 'pending-1').selected?.id,
    'pending-1',
  )
})

test('the daemon attaching a resumed pane itself does not open a second tab', () => {
  // Unlike a spawn, the resume route writes the layout server-side, so the client never
  // PATCHes one back for it. Fleet reconciliation still re-places the resolved id on
  // every refresh until the request is retired, against a layout that already holds it.
  const attached = openTab(twoTabs(), 'first', terminalLeaf('resumed'))
  const replaced = placePendingTerminal(attached, 'resumed', placement)
  assert.deepEqual(terminalIds(replaced), ['first', 'second', 'resumed'])
  assert.equal(stackForView(replaced, 'resumed')?.active_child_id, 'resumed')
})

test('selecting an ordinary pending terminal activates its existing pane tab', () => {
  const withPending = placePendingTerminal(twoTabs(), 'pending-1', placement)
  const movedAway = activateContainingStack(withPending, 'second')
  assert.equal(
    stackForView(selectPendingTerminal(movedAway, 'pending-1'), 'pending-1')?.active_child_id,
    'pending-1',
  )
})
