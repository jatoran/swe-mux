import assert from 'node:assert/strict'
import test from 'node:test'
import {
  isActiveLand,
  landStateLabel,
  landStateTone,
  parseLandQueue,
  parseLandVerifyCommand,
} from '../src/gitLand.ts'

test('a land row parses with its detail and its two OIDs', () => {
  const queue = parseLandQueue({
    hourly_budget: 12,
    hold_timeout_seconds: 1800,
    retry_verification: false,
    requests: [
      {
        id: 'lnd_1',
        project_id: 'p1',
        project_root: 'D:/repo',
        worktree_root: 'D:/wt/alpha',
        branch: 'worktree-alpha',
        trunk_ref: 'main',
        origin: 'agent',
        origin_session_id: 'sess-1',
        state: 'handed_back',
        reason: 'merging main into worktree-alpha conflicted in 1 file(s)',
        detail: { step: 'reconcile', paths: ['shared.txt', 7] },
        trunk_before: 'a'.repeat(40),
        landed_oid: '',
        created_at: 100,
        updated_at: 120,
        finished_at: 120,
      },
    ],
  })
  assert.equal(queue.hourlyBudget, 12)
  assert.equal(queue.requests.length, 1)
  const [row] = queue.requests
  assert.equal(row.branch, 'worktree-alpha')
  assert.equal(row.origin, 'agent')
  // A non-string path is dropped rather than rendered as "7".
  assert.deepEqual(row.paths, ['shared.txt'])
  assert.equal(row.waitingSince, null)
})

test('a row with an unknown state is dropped rather than rendered blank', () => {
  const queue = parseLandQueue({
    requests: [
      { id: 'lnd_1', state: 'exploded' },
      { id: '', state: 'queued' },
      { id: 'lnd_2', state: 'queued' },
      'not an object',
    ],
  })
  assert.deepEqual(queue.requests.map(row => row.id), ['lnd_2'])
})

test('a malformed queue response is empty rather than throwing', () => {
  for (const raw of [null, undefined, 'text', 42, {}, { requests: 'no' }]) {
    const queue = parseLandQueue(raw)
    assert.deepEqual(queue.requests, [])
    assert.equal(queue.hourlyBudget, 0)
  }
})

test('the gate reads as unapproved unless the daemon says otherwise', () => {
  // The default matters more here than anywhere else in the drawer: a parse that
  // fell back to `approved: true` would draw a green gate over bytes nobody read.
  for (const raw of [null, {}, { configured: true }, { approved: 'yes' }]) {
    assert.equal(parseLandVerifyCommand(raw).approved, false)
  }
  const parsed = parseLandVerifyCommand({
    configured: true,
    source: 'convention',
    display: '.worktree-verify',
    digest: 'abc',
    approved: true,
    previously_approved: true,
    approved_source: 'old',
    current_source: 'new',
  })
  assert.equal(parsed.approved, true)
  assert.equal(parsed.approvedSource, 'old')
  assert.equal(parsed.currentSource, 'new')
})

test('active and finished rows are partitioned by their state', () => {
  const active = ['queued', 'waiting', 'reconciling', 'verifying', 'landing']
  const finished = ['landed', 'handed_back', 'refused', 'cancelled']
  for (const state of active) {
    const queue = parseLandQueue({ requests: [{ id: 'x', state }] })
    assert.equal(isActiveLand(queue.requests[0]), true, state)
  }
  for (const state of finished) {
    const queue = parseLandQueue({ requests: [{ id: 'x', state }] })
    assert.equal(isActiveLand(queue.requests[0]), false, state)
  }
})

test('every state has a label, and a hold does not read as a failure', () => {
  const states = [
    'queued', 'waiting', 'reconciling', 'verifying', 'landing',
    'landed', 'handed_back', 'refused', 'cancelled',
  ] as const
  for (const state of states) assert.ok(landStateLabel(state).length > 0, state)
  // A waiting row is a hold with a cause, not a failure: an agent that asked to land
  // and kept working is the common case, and a warn tone there would train the
  // operator to intervene where nothing is wrong.
  assert.equal(landStateTone('waiting'), 'idle')
  assert.equal(landStateTone('landed'), 'ok')
  assert.equal(landStateTone('handed_back'), 'warn')
  assert.equal(landStateTone('refused'), 'warn')
  assert.equal(landStateTone('verifying'), 'busy')
})
