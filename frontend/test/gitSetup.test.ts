import assert from 'node:assert/strict'
import test from 'node:test'
import { parseGitSweMuxSetup } from '../src/gitSetup.ts'

test('Git swe-mux setup parser keeps the applicability distinction', () => {
  assert.deepEqual(parseGitSweMuxSetup({
    show: true, reason: 'tracked', decision: 'unseen', can_ignore: false, tracked: true,
  }), {
    show: true, reason: 'tracked', decision: 'unseen', canIgnore: false, tracked: true,
  })
  assert.equal(parseGitSweMuxSetup({
    show: true, reason: 'invented', decision: 'unseen', can_ignore: true,
  }), null)
  assert.equal(parseGitSweMuxSetup({show: true}), null)
})
