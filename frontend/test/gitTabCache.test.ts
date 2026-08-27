import assert from 'node:assert/strict'
import test from 'node:test'
import {
  GIT_TAB_MEMORY_LIMIT,
  readGitTabMemory,
  resetGitTabMemory,
  writeGitTabMemory,
} from '../src/gitTabCache.ts'

const overview = (root: string) =>
  ({ repositoryRoot: root, worktrees: [] }) as unknown as Parameters<
    typeof writeGitTabMemory
  >[1]['overview']

test('an unknown Project remembers nothing rather than throwing', () => {
  resetGitTabMemory()
  assert.deepEqual(readGitTabMemory('never-seen'), {})
  // The tab renders before a Project is chosen, so this is a real call and not a guard
  // against a bug: seeding from `project?.id` hands this `undefined` on every mount.
  assert.deepEqual(readGitTabMemory(undefined), {})
})

test('writes merge rather than replace, so one reading cannot erase another', () => {
  resetGitTabMemory()
  writeGitTabMemory('p', { overview: overview('C:/repo') })
  writeGitTabMemory('p', { expandedTree: 'C:/repo/.claude/worktrees/one' })
  const remembered = readGitTabMemory('p')

  // The reader's position arrives on its own effect, long after the overview that the
  // fetch wrote. Replacing would mean whichever landed last won.
  assert.equal(remembered.expandedTree, 'C:/repo/.claude/worktrees/one')
  assert.ok(remembered.overview)
})

test('writing to one Project leaves another Project untouched', () => {
  resetGitTabMemory()
  writeGitTabMemory('a', { treeFilter: 'land' })
  writeGitTabMemory('b', { treeFilter: 'codex' })

  assert.equal(readGitTabMemory('a').treeFilter, 'land')
  assert.equal(readGitTabMemory('b').treeFilter, 'codex')
})

test('a Project with no id is not remembered under an empty key', () => {
  resetGitTabMemory()
  writeGitTabMemory(undefined, { treeFilter: 'nowhere' })
  assert.deepEqual(readGitTabMemory(''), {})
})

test('the oldest Project is evicted, and re-writing one keeps it', () => {
  resetGitTabMemory()
  for (let index = 0; index < GIT_TAB_MEMORY_LIMIT; index += 1) {
    writeGitTabMemory(`p${index}`, { treeFilter: String(index) })
  }
  // Touching the oldest entry makes it the newest, which is the whole reason the write
  // re-inserts instead of mutating in place.
  writeGitTabMemory('p0', { treeFilter: 'still here' })
  writeGitTabMemory('overflow', { treeFilter: 'new' })

  assert.equal(readGitTabMemory('p0').treeFilter, 'still here')
  assert.deepEqual(readGitTabMemory('p1'), {})
  assert.equal(readGitTabMemory('overflow').treeFilter, 'new')
})

test('resetting drops every Project', () => {
  resetGitTabMemory()
  writeGitTabMemory('p', { treeFilter: 'gone' })
  resetGitTabMemory()
  assert.deepEqual(readGitTabMemory('p'), {})
})
