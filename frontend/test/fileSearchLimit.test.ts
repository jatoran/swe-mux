import assert from 'node:assert/strict'
import test from 'node:test'

import { describeStopPoint, searchTruncationNotice } from '../src/fileSearchLimit.ts'

test('a complete search says nothing', () => {
  assert.equal(searchTruncationNotice({ truncated: false }, 12), null)
  assert.equal(searchTruncationNotice(null, 12), null)
  assert.equal(searchTruncationNotice(undefined, 0), null)
})

test('the result limit is the case where refining actually helps', () => {
  assert.equal(
    searchTruncationNotice({ truncated: true, truncated_reason: 'results' }, 300),
    'Showing the first 300 matches; refine to narrow.',
  )
})

test('the file limit names where it stopped and does not tell the reader to refine', () => {
  // Refining re-runs the same walk and gives up in the same place, so "refine to narrow"
  // sends the reader retyping instead of fixing the ignore list.
  const notice = searchTruncationNotice(
    { truncated: true, truncated_reason: 'files', stopped_at: '.claude/worktrees' },
    26,
  )
  assert.match(notice ?? '', /\.claude\/worktrees/)
  assert.match(notice ?? '', /ignore pattern/)
  assert.doesNotMatch(notice ?? '', /refine/i)
})

test('stopping at the root is named in words rather than as an empty string', () => {
  assert.equal(describeStopPoint(''), 'the Project root')
  assert.equal(describeStopPoint(null), 'the Project root')
  assert.equal(describeStopPoint('  '), 'the Project root')
  assert.equal(describeStopPoint('src'), 'src')
  const notice = searchTruncationNotice({ truncated: true, truncated_reason: 'files' }, 0)
  assert.match(notice ?? '', /the Project root/)
})

test('a daemon predating the reason field still gets a notice', () => {
  // The redeploy rollback path makes an older daemon real, and a notice is all the reader
  // gets: going silent there is worse than the message the old build always meant.
  assert.equal(
    searchTruncationNotice({ truncated: true }, 300),
    'Showing the first 300 matches; refine to narrow.',
  )
})
