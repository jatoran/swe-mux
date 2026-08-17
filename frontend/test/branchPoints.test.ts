import assert from 'node:assert/strict'
import test from 'node:test'

import {
  branchActionLabel,
  branchIneligibilityMessage,
  branchModeFor,
  branchOutcomeSummary,
  branchPointEligible,
  branchPointPreview,
  branchPointsEmptyMessage,
  defaultBranchPoint,
  orderedBranchPoints,
  type BranchPoint,
} from '../src/branchPoints.ts'
import {
  forgetBranchSeed,
  MAX_STAGED_BRANCH_SEEDS,
  stageBranchSeed,
  stagedBranchSeedCount,
  takeBranchSeed,
} from '../src/branchSeed.ts'

const point = (
  ordinal: number,
  role: 'user' | 'assistant',
  overrides: Partial<BranchPoint> = {},
): BranchPoint => ({
  message_id: `offset:${ordinal * 100}`,
  ordinal,
  role,
  ts: null,
  text: `message ${ordinal}`,
  default_mode: role === 'user' ? 'before' : 'after',
  modes: {
    before: { eligible: ordinal > 0, reason: ordinal > 0 ? null : 'outside_window' },
    after: { eligible: true, reason: null },
  },
  ...overrides,
})

test('a prompt is redone and a reply is continued from', () => {
  // The two are opposite acts on opposite kinds of message, so the default follows
  // the role rather than making the common case a decision.
  assert.equal(branchModeFor(point(1, 'user')), 'before')
  assert.equal(branchModeFor(point(2, 'assistant')), 'after')
  assert.equal(branchActionLabel('before'), 'Branch before this')
  assert.equal(branchActionLabel('after'), 'Branch after this')
  assert.match(branchOutcomeSummary('before'), /comes back for you to edit/)
  assert.match(branchOutcomeSummary('after'), /including this reply/)
})

test('the newest message is shown first', () => {
  const ordered = orderedBranchPoints([point(0, 'user'), point(1, 'assistant'), point(2, 'user')])
  assert.deepEqual(ordered.map(item => item.ordinal), [2, 1, 0])
})

test('the picker opens on the newest point its own cut is available at', () => {
  const blocked = point(2, 'assistant', {
    modes: {
      before: { eligible: true, reason: null },
      after: { eligible: false, reason: 'unanswered_tool_calls' },
    },
  })
  const points = [point(0, 'user'), point(1, 'assistant'), blocked]
  assert.equal(defaultBranchPoint(points)?.ordinal, 1)
})

test('a conversation with no usable point opens on none rather than on a refusal', () => {
  const only = point(0, 'user')
  assert.equal(branchPointEligible(only, 'before'), false)
  assert.equal(defaultBranchPoint([only]), null)
  assert.equal(defaultBranchPoint([]), null)
})

test('an ineligible point says what is wrong with it, not that it is unavailable', () => {
  // The reader can see the message; hiding the reason leaves them wondering why a
  // point in front of them is not offered.
  assert.match(branchIneligibilityMessage('unanswered_tool_calls'), /would not load/)
  assert.match(branchIneligibilityMessage('outside_window'), /Nothing loaded before/)
  assert.equal(branchIneligibilityMessage(null), '')
  // An unknown code still reaches the reader rather than rendering blank.
  assert.match(branchIneligibilityMessage('something_new'), /something_new/)
})

test('an empty list explains itself in the words of its cause', () => {
  assert.match(branchPointsEmptyMessage('no_transcript', 'claude'), /first message/)
  assert.match(branchPointsEmptyMessage('dialect_unsupported', 'opencode'), /opencode/)
  assert.match(branchPointsEmptyMessage('strategy_has_no_points', 'codex'), /stands now/)
  assert.match(branchPointsEmptyMessage(null, 'claude'), /Nothing has been said/)
})

test('a preview is one bounded line, whatever the message looked like', () => {
  assert.equal(branchPointPreview('  two\n\nlines  '), 'two lines')
  assert.equal(branchPointPreview('abcdef', 4), 'abc…')
  assert.equal(branchPointPreview(''), '')
})

test('a staged prompt is handed over exactly once', () => {
  // The pane replays again on every reconnect. Re-inserting a prompt the operator has
  // already edited or sent is worse than losing it.
  stageBranchSeed('pane-1', 'second prompt')
  assert.equal(takeBranchSeed('pane-1'), 'second prompt')
  assert.equal(takeBranchSeed('pane-1'), '')
})

test('nothing is staged for an empty prompt or an unnamed pane', () => {
  stageBranchSeed('pane-2', null)
  stageBranchSeed('pane-2', '')
  stageBranchSeed('', 'orphan')
  assert.equal(takeBranchSeed('pane-2'), '')
  assert.equal(takeBranchSeed(''), '')
})

test('a pane that never opens cannot grow the staging map without bound', () => {
  for (let index = 0; index < MAX_STAGED_BRANCH_SEEDS + 4; index += 1) {
    stageBranchSeed(`abandoned-${index}`, 'never claimed')
  }
  assert.equal(stagedBranchSeedCount(), MAX_STAGED_BRANCH_SEEDS)
  // The oldest went first, so the most recent branch is the one still holding a seed.
  assert.equal(takeBranchSeed('abandoned-0'), '')
  assert.equal(takeBranchSeed(`abandoned-${MAX_STAGED_BRANCH_SEEDS + 3}`), 'never claimed')
  for (let index = 0; index < MAX_STAGED_BRANCH_SEEDS + 4; index += 1) {
    forgetBranchSeed(`abandoned-${index}`)
  }
  assert.equal(stagedBranchSeedCount(), 0)
})
