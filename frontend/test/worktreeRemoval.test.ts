import assert from 'node:assert/strict'
import test from 'node:test'
import {
  assessRemoval,
  beginRemovals,
  forgetRemoval,
  isRemoving,
  landBlockLabel,
  planBulkLand,
  planBulkRemoval,
  removalBlockLabel,
  removalWarningLabel,
  settleRemovals,
  skippedLabel,
} from '../src/worktreeRemoval.ts'
import type { GitOverviewWorktree, ReviewChangeSummary } from '../src/gitWorktrees.ts'

const summary = (total: number): ReviewChangeSummary =>
  ({ total, additions: total, deletions: 0, binaryFiles: 0, files: [], truncated: false })

function tree(overrides: Partial<GitOverviewWorktree> = {}): GitOverviewWorktree {
  return {
    path: 'D:\\work\\wt-a',
    head: 'a'.repeat(40),
    branch: 'wt-a',
    detached: false,
    bare: false,
    locked: null,
    prunable: null,
    main: false,
    headCommittedAt: 1786800000,
    comparisonCounts: { ahead: 0, behind: 0 },
    unstaged: summary(0),
    staged: summary(0),
    conflicted: summary(0),
    branchDelta: summary(0),
    ...overrides,
  }
}

test('a removing worktree stays pending until the refreshed inventory drops it', () => {
  // The response is not what ends it. On the fast path the daemon answers before Git
  // has deleted a byte; on the slow path it answers while Git still is.
  let pending = beginRemovals({}, ['D:\\work\\wt-a'])
  assert.equal(isRemoving(pending, 'D:/work/wt-a'), true)
  pending = settleRemovals(pending, [{ path: 'D:\\work\\wt-a' }, { path: 'D:\\work\\wt-b' }])
  assert.equal(isRemoving(pending, 'D:\\work\\wt-a'), true)
  pending = settleRemovals(pending, [{ path: 'D:\\work\\wt-b' }])
  assert.deepEqual(pending, {})
})

test('separators and case cannot hide a pending removal', () => {
  const pending = beginRemovals({}, ['D:\\Work\\WT-A'])
  assert.equal(isRemoving(pending, 'd:/work/wt-a'), true)
  assert.deepEqual(settleRemovals(pending, [{ path: 'd:/work/wt-a/' }]), pending)
})

test('a refusal clears only its own entry', () => {
  const pending = beginRemovals({}, ['D:\\work\\wt-a', 'D:\\work\\wt-b'])
  const after = forgetRemoval(pending, 'D:\\work\\wt-a')
  assert.equal(isRemoving(after, 'D:\\work\\wt-a'), false)
  assert.equal(isRemoving(after, 'D:\\work\\wt-b'), true)
  // Nothing to forget is the same object, so the list does not re-render for it.
  assert.equal(forgetRemoval(after, 'D:\\work\\wt-a'), after)
})

test('the main tree, a live session, and a lock each block removal', () => {
  assert.deepEqual(assessRemoval(tree({ main: true }), 0).blocks, ['main'])
  assert.deepEqual(assessRemoval(tree(), 2).blocks, ['live_session'])
  assert.deepEqual(assessRemoval(tree({ locked: '' }), 0).blocks, ['locked'])
  assert.deepEqual(assessRemoval(tree(), 0).blocks, [])
})

test('uncommitted files need force; unlanded commits are a warning force cannot answer', () => {
  const dirty = assessRemoval(tree({ unstaged: summary(3) }), 0)
  assert.deepEqual(dirty.warnings, ['dirty'])
  assert.equal(dirty.needsForce, true)

  const unlanded = assessRemoval(tree({ comparisonCounts: { ahead: 4, behind: 0 } }), 0)
  assert.deepEqual(unlanded.warnings, ['unlanded'])
  assert.equal(unlanded.unlanded, 4)
  // Force discards files. It does not make four commits exist anywhere else, which is
  // why the confirmation names both and the flag only claims the first.
  assert.equal(unlanded.needsForce, false)
})

test('an unmeasured checkout is never reported as clean', () => {
  // A prunable worktree measures nothing, and an unavailable comparison ref measures no
  // divergence. Both are stated, and both take the safe side of "does Git need force".
  const unreadable = assessRemoval(tree({ unstaged: null, comparisonCounts: null }), 0)
  assert.deepEqual(unreadable.warnings, ['unmeasured'])
  assert.equal(unreadable.needsForce, true)
  assert.equal(unreadable.unlanded, null)
})

test('a bulk removal separates what it will do, refuse, and have to be told about', () => {
  const plan = planBulkRemoval([
    assessRemoval(tree({ path: 'D:\\work\\clean' }), 0),
    assessRemoval(tree({ path: 'D:\\work\\dirty', staged: summary(2) }), 0),
    assessRemoval(tree({ path: 'D:\\work\\busy' }), 1),
    assessRemoval(tree({ path: 'D:\\work\\locked', locked: 'held' }), 0),
  ])
  assert.deepEqual(plan.removable.map(item => item.path), ['D:\\work\\clean', 'D:\\work\\dirty'])
  assert.deepEqual(plan.blocked.map(item => item.path), ['D:\\work\\busy', 'D:\\work\\locked'])
  assert.deepEqual(plan.warned.map(item => item.path), ['D:\\work\\dirty'])
  assert.equal(plan.needsForce, true)
})

test('bulk land takes every named branch in map order and refuses the other two cases', () => {
  const plan = planBulkLand([
    { path: 'D:\\repo', branch: 'master', main: true },
    { path: 'D:\\work\\wt-b', branch: 'wt-b', main: false },
    { path: 'D:\\work\\loose', branch: null, main: false },
    { path: 'D:\\work\\wt-a', branch: 'wt-a', main: false },
  ])
  assert.deepEqual(plan.landable.map(item => item.branch), ['wt-b', 'wt-a'])
  assert.deepEqual(plan.blocked, [
    { path: 'D:\\repo', reason: 'main' },
    { path: 'D:\\work\\loose', reason: 'detached' },
  ])
})

test('the reasons read as words a reader can check', () => {
  assert.equal(removalBlockLabel('live_session'), 'in use')
  assert.equal(removalWarningLabel('unlanded'), 'unlanded')
  assert.equal(landBlockLabel('detached'), 'detached HEAD')
  assert.equal(skippedLabel({ live_session: 2, locked: 1 }), 'skipping 2 in use, 1 locked')
  assert.equal(skippedLabel({ live_session: 0 }), '')
})
