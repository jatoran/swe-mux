import assert from 'node:assert/strict'
import test from 'node:test'
import { mergeSessionSnapshot, reconcileSessionSnapshots } from '../src/sessionSnapshots.ts'
import type { Session } from '../src/types.ts'

const session = (extra: Partial<Session> = {}) => ({
  id: 's1', name: 'codex-s1', project_id: 'p1', backend: 'codex',
  state: 'idle', agent_run_id: 'r1', last_activity_ts: 1,
  ...extra,
}) as unknown as Session

test('a raw PTY update preserves the generated title for the same run', () => {
  const current = session({
    generated_title: 'Session notes redesign',
    _snapshot_generation: 'g1', _snapshot_revision: 4, _snapshot_enriched: true,
  })
  const incoming = session({
    state: 'working', _snapshot_generation: 'g1', _snapshot_revision: 5,
    _snapshot_enriched: false,
  })
  const merged = mergeSessionSnapshot(current, incoming)
  assert.equal(merged.state, 'working')
  assert.equal(merged.generated_title, 'Session notes redesign')
})

test('an older same-generation snapshot cannot regress core status', () => {
  const current = session({
    state: 'working', _snapshot_generation: 'g1', _snapshot_revision: 9,
  })
  const stale = session({
    state: 'idle', _snapshot_generation: 'g1', _snapshot_revision: 8,
  })
  assert.equal(mergeSessionSnapshot(current, stale).state, 'working')
})

test('a stale enriched response may add presentation without regressing state', () => {
  const current = session({
    state: 'working', _snapshot_generation: 'g1', _snapshot_revision: 9,
  })
  const stale = session({
    state: 'idle', generated_title: 'Stable title',
    _snapshot_generation: 'g1', _snapshot_revision: 8, _snapshot_enriched: true,
  })
  const merged = mergeSessionSnapshot(current, stale)
  assert.equal(merged.state, 'working')
  assert.equal(merged.generated_title, 'Stable title')
})

test('a new daemon generation resets the revision domain', () => {
  const current = session({
    state: 'working', _snapshot_generation: 'old', _snapshot_revision: 100,
  })
  const restarted = session({
    state: 'idle', _snapshot_generation: 'new', _snapshot_revision: 0,
  })
  assert.equal(mergeSessionSnapshot(current, restarted).state, 'idle')
})

test('fleet reconciliation removes absent sessions and preserves optimistic rows', () => {
  const current = [session(), session({ id: 'gone' })]
  const incoming = [session({ state: 'working' })]
  const pending = [session({ id: 'pending', pending: true })]
  assert.deepEqual(
    reconcileSessionSnapshots(current, incoming, pending).map(item => item.id),
    ['s1', 'pending'],
  )
})
