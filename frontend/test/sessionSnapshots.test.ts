import assert from 'node:assert/strict'
import test from 'node:test'
import {
  applySessionRowPatch, mergeSessionSnapshot, reconcileSessionSnapshots,
} from '../src/sessionSnapshots.ts'
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

const versioned = (revision: number, extra: Partial<Session> = {}) => session({
  cost_usd: 1, context_pct: 0.1, state: 'working',
  _snapshot_generation: 'g1', _snapshot_revision: revision, _snapshot_enriched: true,
  ...extra,
})

const patchOf = (revision: number, fields: Record<string, unknown>) => ({ fields, revision })

test('a newer row patch writes its fields without claiming the rest of the row is new', () => {
  const current = versioned(5)
  const patched = applySessionRowPatch(current, patchOf(7, { cost_usd: 2, context_pct: 0.4 }), 'g1')
  assert.ok(patched)
  assert.equal(patched.cost_usd, 2)
  assert.equal(patched.context_pct, 0.4)
  assert.equal(patched.state, 'working')
  // Every other field is still as old as revision 5, so a snapshot at 6 must still merge.
  assert.equal(patched._snapshot_revision, 5)
  assert.deepEqual(patched._field_revisions, { cost_usd: 7, context_pct: 7 })
  assert.equal(current.cost_usd, 1, 'the row it was given is not mutated')
})

test('a patch the row already contains changes nothing', () => {
  const current = versioned(7)
  assert.equal(applySessionRowPatch(current, patchOf(7, { cost_usd: 99 }), 'g1'), current)
  assert.equal(applySessionRowPatch(current, patchOf(3, { cost_usd: 99 }), 'g1'), current)
})

test('a patch from another daemon generation cannot be ordered, so the row is re-read instead', () => {
  assert.equal(applySessionRowPatch(versioned(5), patchOf(9, { cost_usd: 2 }), 'g2'), null)
  assert.equal(applySessionRowPatch(versioned(5), patchOf(9, { cost_usd: 2 }), null), null)
  const unversioned = session({ cost_usd: 1 })
  assert.equal(applySessionRowPatch(unversioned, patchOf(9, { cost_usd: 2 }), 'g1'), null)
})

test('an older patch to a field never overwrites a newer one', () => {
  const newer = applySessionRowPatch(versioned(5), patchOf(9, { cost_usd: 3 }), 'g1')
  assert.ok(newer)
  const older = applySessionRowPatch(newer, patchOf(7, { cost_usd: 2, context_pct: 0.5 }), 'g1')
  assert.ok(older)
  assert.equal(older.cost_usd, 3)
  assert.equal(older.context_pct, 0.5, 'a field the newer patch did not carry still applies')
  assert.deepEqual(older._field_revisions, { cost_usd: 9, context_pct: 7 })
})

test('a patch cannot move a row between sessions or Projects, or forge its ordering', () => {
  const patched = applySessionRowPatch(versioned(5), patchOf(9, {
    id: 'other', project_id: 'p2', _snapshot_revision: 100, _field_revisions: {}, cost_usd: 2,
  }), 'g1')
  assert.ok(patched)
  assert.equal(patched.id, 's1')
  assert.equal(patched.project_id, 'p1')
  assert.equal(patched._snapshot_revision, 5)
  assert.deepEqual(patched._field_revisions, { cost_usd: 9 })
})

test('a snapshot older than a patch merges but keeps the patched fields', () => {
  const patched = applySessionRowPatch(versioned(5), patchOf(9, { cost_usd: 2 }), 'g1')
  assert.ok(patched)
  // The fleet list read at revision 7: newer than the row, older than the patch.
  const incoming = versioned(7, { cost_usd: 1, state: 'idle' })
  const merged = mergeSessionSnapshot(patched, incoming)
  assert.equal(merged.state, 'idle', 'everything else the snapshot says is newer')
  assert.equal(merged.cost_usd, 2, 'the patch is newer than the snapshot for its own field')
  assert.equal(merged._snapshot_revision, 7)
  assert.deepEqual(merged._field_revisions, { cost_usd: 9 })
  assert.equal(incoming.cost_usd, 1, 'the incoming snapshot is not mutated')
  assert.equal(incoming._field_revisions, undefined)
})

test('a snapshot at or past a patch supersedes it', () => {
  const patched = applySessionRowPatch(versioned(5), patchOf(9, { cost_usd: 2 }), 'g1')
  assert.ok(patched)
  const caughtUp = mergeSessionSnapshot(patched, versioned(9, { cost_usd: 2.5 }))
  assert.equal(caughtUp.cost_usd, 2.5)
  assert.equal(caughtUp._field_revisions, undefined)
  const stale = mergeSessionSnapshot(patched, versioned(4, { cost_usd: 0 }))
  assert.equal(stale.cost_usd, 2, 'a snapshot older than the row changes neither')
})

test('a new daemon generation drops every patch along with the old revision domain', () => {
  const patched = applySessionRowPatch(versioned(5), patchOf(9, { cost_usd: 2 }), 'g1')
  assert.ok(patched)
  const restarted = mergeSessionSnapshot(patched, versioned(1, { cost_usd: 0, _snapshot_generation: 'g2' }))
  assert.equal(restarted.cost_usd, 0)
  assert.equal(restarted._field_revisions, undefined)
})

test('an unversioned mutation response leaves the patches standing', () => {
  const patched = applySessionRowPatch(versioned(5), patchOf(9, { cost_usd: 2 }), 'g1')
  assert.ok(patched)
  const response = session({ cost_usd: 1, name: 'renamed' })
  const merged = mergeSessionSnapshot(patched, response)
  assert.equal(merged.name, 'renamed')
  assert.equal(merged.cost_usd, 2)
  assert.equal(merged._snapshot_revision, 5)
  assert.deepEqual(merged._field_revisions, { cost_usd: 9 })
})

test('fleet reconciliation keeps a newer patch on its row', () => {
  const patched = applySessionRowPatch(versioned(5), patchOf(9, { cost_usd: 2 }), 'g1')
  assert.ok(patched)
  const [row] = reconcileSessionSnapshots([patched], [versioned(6, { cost_usd: 1 })])
  assert.equal(row.cost_usd, 2)
})
