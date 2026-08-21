import assert from 'node:assert/strict'
import test from 'node:test'
import { describeAge, describeStatus, recentEntryTitle } from '../src/recentFiles.ts'

test('a porcelain code reads as a word, from either column', () => {
  assert.equal(describeStatus('??'), 'new')
  assert.equal(describeStatus(' M'), 'modified')
  assert.equal(describeStatus('M '), 'modified')
  // Staged and then edited again: still one thing to a reader.
  assert.equal(describeStatus('MM'), 'modified')
  assert.equal(describeStatus('A '), 'added')
  assert.equal(describeStatus('R '), 'renamed')
  assert.equal(describeStatus('UU'), 'conflicted')
  assert.equal(describeStatus(null), 'changed')
})

test('a conflict outranks the modification beside it', () => {
  // `UU` and `AU`/`UD` all mean the same thing to someone deciding what to open next.
  assert.equal(describeStatus('AU'), 'conflicted')
  assert.equal(describeStatus('UD'), 'conflicted')
})

test('age is coarse and never negative', () => {
  assert.equal(describeAge(1000, 1000), 'just now')
  assert.equal(describeAge(1000, 1060), 'just now')
  assert.equal(describeAge(1000, 1000 + 60 * 10), '10m ago')
  assert.equal(describeAge(1000, 1000 + 3600 * 5), '5h ago')
  assert.equal(describeAge(1000, 1000 + 86400 * 3), '3d ago')
  assert.equal(describeAge(1000, 1000 + 86400 * 60), '2mo ago')
  assert.equal(describeAge(1000, 1000 + 86400 * 400), '1y ago')
  // A committer clock ahead of this machine's must not render as a future age.
  assert.equal(describeAge(2000, 1000), 'just now')
})

test('a working-tree row states what changed and a committed row states when', () => {
  assert.equal(
    recentEntryTitle({ origin: 'working', status: '??', committed_at: null }),
    'new · uncommitted',
  )
  assert.equal(
    recentEntryTitle({ origin: 'committed', status: null, committed_at: 1000 }, 1000 + 3600),
    '1h ago',
  )
})

test('a committed row with no readable date says so rather than inventing one', () => {
  assert.equal(
    recentEntryTitle({ origin: 'committed', status: null, committed_at: null }),
    'committed',
  )
})
