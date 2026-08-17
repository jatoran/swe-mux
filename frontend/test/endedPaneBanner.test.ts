import assert from 'node:assert/strict'
import test from 'node:test'
import {
  canRestartCold, checkpointAge, coldSessionSummary, isColdSession, missingContentReason,
} from '../src/coldSession.ts'
import { reconcileTerminals, openTab, terminalIds, terminalLeaf, type PaneLayout } from '../src/layout.ts'
import type { Session } from '../src/types.ts'

const session = (id: string, extra: Partial<Session> = {}) => ({
  id, name: id, project_id: 'p1', backend: 'shell', state: 'idle', last_activity_ts: 1,
  ...extra,
}) as unknown as Session

const emptyLayout: PaneLayout = { version: 7, root: null }
const stacked = (...ids: string[]): PaneLayout =>
  ids.reduce<PaneLayout>((layout, id) => openTab(layout, null, terminalLeaf(id)), emptyLayout)

test('an ended session keeps its pane, and only leaving the fleet removes it', () => {
  // The defect this fixes: a session that ended on its own kept its sidebar row
  // and lost its tab in the same instant, so the pane showing what it printed was
  // destroyed at exactly the moment somebody wanted to read it — and the pruned
  // layout was written back, so it did not come back.
  const layout = stacked('live', 'ended', 'cold')
  const fleet = [
    session('live'),
    session('ended', { state: 'exited' }),
    session('cold', { state: 'crashed', cold: true }),
  ]
  const kept = reconcileTerminals(layout, new Set(fleet.map(item => item.id)))
  assert.deepEqual(terminalIds(kept).sort(), ['cold', 'ended', 'live'])

  // Killed or dismissed — gone from the fleet — is what prunes the leaf.
  const afterDismiss = reconcileTerminals(layout, new Set(['live']))
  assert.deepEqual(terminalIds(afterDismiss), ['live'])
})

test('a missing-content reason is only given for a reason the daemon named', () => {
  assert.equal(missingContentReason(session('a')), null)
  assert.match(
    missingContentReason(session('b', { cold_terminal_skipped: 'alternate_screen_harness' })) ?? '',
    /full-screen/i,
  )
  assert.match(
    missingContentReason(session('c', { cold_terminal_skipped: 'alternate_screen' })) ?? '',
    /full-screen/i,
  )
  assert.match(
    missingContentReason(session('d', { cold_terminal_skipped: 'repaints_scrollback' })) ?? '',
    /repaints/i,
  )
  // An unfamiliar reason from a newer daemon still says something true.
  assert.equal(
    missingContentReason(session('e', { cold_terminal_skipped: 'something_new' })),
    'No terminal content was captured.',
  )
})

test('checkpoint age is coarse, and never claims more precision than it has', () => {
  const now = 1_000_000_000_000
  const at = (secondsAgo: number) => checkpointAge(now / 1000 - secondsAgo, now)
  assert.equal(at(5), 'seconds before the shutdown')
  assert.equal(at(60), 'seconds before the shutdown')
  assert.equal(at(600), '10 minutes before this restart')
  assert.equal(at(3600), '60 minutes before this restart')
  assert.equal(at(6 * 3600), '6 hours before this restart')
  assert.equal(at(4 * 86400), '4 days before this restart')
  // A checkpoint stamped in the future (clock change) must not read as negative.
  assert.equal(checkpointAge(now / 1000 + 500, now), 'seconds before the shutdown')
})

test('a recovered agent is resumed and a recovered shell is restarted', () => {
  // Replaying an agent's argv would start a fresh conversation while re-injecting
  // the old one's --session-id, where the operator asked to return to it.
  const shell = session('a', { state: 'crashed', cold: true, backend: 'shell' })
  const agent = session('b', { state: 'crashed', cold: true, backend: 'claude' })
  const exited = session('c', { state: 'exited', backend: 'shell' })
  assert.equal(canRestartCold(shell), true)
  assert.equal(canRestartCold(agent), false)
  // An ordinary exit is not a recovery: the daemon saw it, and nothing was rebuilt.
  assert.equal(isColdSession(exited), false)
  assert.equal(canRestartCold(exited), false)
})

test('the row tooltip always accounts for the pane content, present or not', () => {
  const now = 1_000_000_000_000
  const withContent = session('a', { cold: true, cold_terminal_at: now / 1000 - 600 })
  assert.match(coldSessionSummary(withContent, now), /Recovered after an unexpected shutdown/)
  assert.match(coldSessionSummary(withContent, now), /10 minutes before this restart/)
  const withoutContent = session('b', {
    cold: true, cold_terminal_skipped: 'alternate_screen_harness',
  })
  assert.match(coldSessionSummary(withoutContent, now), /full-screen/i)
})
