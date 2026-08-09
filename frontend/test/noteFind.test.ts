import assert from 'node:assert/strict'
import test from 'node:test'
import { findMatches, findStepDirection, matchIndexAfter, stepMatchIndex } from '../src/noteFind.ts'
import { byteForUtf16Index } from '../src/noteSelection.ts'

test('UTF-16 indices convert to the byte offsets Continuity ranges are expressed in', () => {
  // "é" is two UTF-8 bytes, "→" three, "𝄞" four (and two UTF-16 units).
  assert.equal(byteForUtf16Index('abc', 0), 0)
  assert.equal(byteForUtf16Index('abc', 2), 2)
  assert.equal(byteForUtf16Index('éa', 1), 2)
  assert.equal(byteForUtf16Index('a→b', 2), 4)
  assert.equal(byteForUtf16Index('𝄞x', 2), 4)
  // Past the end clamps to the whole string rather than running off it.
  assert.equal(byteForUtf16Index('a→b', 99), 5)
  assert.equal(byteForUtf16Index('abc', -3), 0)
})

test('matches are found per line and reported as byte ranges', () => {
  const ranges = findMatches('alpha beta\ngamma beta', 'beta')
  assert.deepEqual(ranges, [
    { start: { line: 0, byteInLine: 6 }, end: { line: 0, byteInLine: 10 } },
    { start: { line: 1, byteInLine: 6 }, end: { line: 1, byteInLine: 10 } },
  ])
})

test('a match after a multi-byte character is offset in bytes, not characters', () => {
  const ranges = findMatches('é→ hit', 'hit')
  // "é" is 2 bytes, "→" is 3, plus the space: the match starts at byte 6, not index 3.
  assert.deepEqual(ranges, [{ start: { line: 0, byteInLine: 6 }, end: { line: 0, byteInLine: 9 } }])
})

test('matching folds case by default and respects case when asked', () => {
  assert.equal(findMatches('Alpha alpha ALPHA', 'alpha').length, 3)
  assert.equal(findMatches('Alpha alpha ALPHA', 'alpha', { matchCase: true }).length, 1)
  assert.equal(findMatches('Alpha alpha ALPHA', 'ALPHA', { matchCase: true }).length, 1)
})

test('overlapping occurrences are reported once, scanning forward', () => {
  // "aa" in "aaaa" could be read as three overlapping matches; find bars report two.
  assert.deepEqual(
    findMatches('aaaa', 'aa').map(range => range.start.byteInLine),
    [0, 2],
  )
})

test('a query spanning a line break never matches', () => {
  assert.deepEqual(findMatches('one\ntwo', 'one\ntwo'), [])
  assert.deepEqual(findMatches('one\ntwo', 'e\nt'), [])
})

test('an empty query matches nothing rather than everything', () => {
  assert.deepEqual(findMatches('anything at all', ''), [])
})

test('a line whose case folding changes length still reports honest offsets', () => {
  // "İ".toLowerCase() is two UTF-16 units, so folded indices would point past the real
  // ones. That line falls back to case-sensitive matching instead of lying about where
  // the match is.
  const ranges = findMatches('İ hit', 'hit')
  assert.equal(ranges.length, 1)
  assert.equal(ranges[0].start.byteInLine, byteForUtf16Index('İ hit', 2))
})

test('the first match shown is the one at or after the caret', () => {
  const ranges = findMatches('beta\nbeta\nbeta', 'beta')
  assert.equal(matchIndexAfter(ranges, { line: 0, byteInLine: 0 }), 0)
  assert.equal(matchIndexAfter(ranges, { line: 1, byteInLine: 0 }), 1)
  // A caret past every match wraps to the top rather than reporting nothing.
  assert.equal(matchIndexAfter(ranges, { line: 9, byteInLine: 0 }), 0)
  assert.equal(matchIndexAfter(ranges, null), 0)
  assert.equal(matchIndexAfter([], { line: 3, byteInLine: 0 }), 0)
})

test('stepping through matches wraps at both ends', () => {
  assert.equal(stepMatchIndex(3, 0, false), 1)
  assert.equal(stepMatchIndex(3, 2, false), 0)
  assert.equal(stepMatchIndex(3, 0, true), 2)
  assert.equal(stepMatchIndex(0, 0, false), 0)
})

test('F3 steps forward and Shift+F3 steps backward', () => {
  assert.equal(findStepDirection('F3', false), 'next')
  assert.equal(findStepDirection('F3', true), 'previous')
  assert.equal(findStepDirection('f3', false), null)
  assert.equal(findStepDirection('Enter', false), null)
})
