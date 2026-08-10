import assert from 'node:assert/strict'
import test from 'node:test'
import { normalizeWorktreeBranchInput, worktreePathForBranch } from '../src/worktreeLaunch.ts'

test('branch input turns whitespace into git-safe separators', () => {
  assert.equal(normalizeWorktreeBranchInput('haha hehe'), 'haha-hehe')
  assert.equal(normalizeWorktreeBranchInput('worktree\tmy change'), 'worktree-my-change')
})

test('a new Windows worktree is grouped below the configured root', () => {
  assert.equal(
    worktreePathForBranch('C:\\Users\\me\\.mux\\worktrees', 'swe mux', '12345678-abcd', 'agent/run-menu'),
    'C:\\Users\\me\\.mux\\worktrees\\swe-mux-12345678\\agent-run-menu',
  )
})

test('worktree path suggestions are safe leaf names on either platform', () => {
  assert.equal(
    worktreePathForBranch('/home/me/.mux/worktrees/', 'mux: app', 'project/id', ' feature: one. '),
    '/home/me/.mux/worktrees/mux-app-project/feature-one',
  )
  assert.equal(
    worktreePathForBranch('/home/me/.mux/worktrees', 'mux', '12345678', ''),
    '/home/me/.mux/worktrees/mux-12345678/worktree',
  )
  assert.equal(worktreePathForBranch('   ', 'mux', '12345678', 'feature'), '')
})
