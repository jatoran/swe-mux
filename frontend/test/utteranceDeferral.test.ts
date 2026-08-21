import assert from 'node:assert/strict'
import test from 'node:test'

import { DEFERRAL_MAX_HOLD_MS, DeferralPen } from '../src/utteranceDeferral.ts'

/** A hand-cranked clock, so the hold ceiling is testable without waiting 15 s. */
const clock = () => {
  let value = 0
  return { now: () => value, advance: (ms: number) => { value += ms } }
}

test('a finished utterance dispatches untouched and holds nothing', () => {
  const pen = new DeferralPen(clock().now)
  const routed = pen.offer('  open the alpha session  ')
  assert.deepEqual(routed, { kind: 'dispatch', text: 'open the alpha session', merged: null })
  assert.equal(pen.pending, null)
})

test('an unfinished utterance is held with the trigger that caused it', () => {
  const time = clock()
  time.advance(4_000)
  const pen = new DeferralPen(time.now)
  const routed = pen.offer('open the alpha session and')
  assert.equal(routed.kind, 'defer')
  if (routed.kind !== 'defer') return
  assert.deepEqual(routed.deferred, {
    text: 'open the alpha session and',
    trigger: 'and',
    kind: 'conjunction',
    words: 5,
    at: 4_000,
  })
  assert.equal(pen.pending, routed.deferred)
})

test('the second breath merges into the held fragment and dispatches as one turn', () => {
  const pen = new DeferralPen(clock().now)
  const first = pen.offer('open the alpha session and')
  assert.equal(first.kind, 'defer')
  const second = pen.offer('tell me what it is running')
  assert.deepEqual(second, {
    kind: 'dispatch',
    text: 'open the alpha session and tell me what it is running',
    merged: first.kind === 'defer' ? first.deferred : null,
  })
  assert.equal(pen.pending, null)
})

test('a merge is never re-judged, so fragments cannot compound into an unbounded wait', () => {
  const pen = new DeferralPen(clock().now)
  assert.equal(pen.offer('I want to look at the queue and').kind, 'defer')
  // The continuation is itself unfinished. It still dispatches: at most one
  // deferral per utterance is the whole bound, and re-judging here is exactly
  // how a chain of breaths would hold a turn forever.
  const second = pen.offer('then the transcript for')
  assert.equal(second.kind, 'dispatch')
  assert.equal(second.kind === 'dispatch' && second.text, 'I want to look at the queue and then the transcript for')
  assert.equal(pen.pending, null)
})

test('the release waits while the operator is still mid-thought, then submits', () => {
  const time = clock()
  const pen = new DeferralPen(time.now)
  const routed = pen.offer('send it to')
  assert.equal(routed.kind, 'defer')
  if (routed.kind !== 'defer') return
  time.advance(1_200)
  assert.deepEqual(pen.release(routed.deferred, true), { kind: 'wait' })
  assert.equal(pen.pending, routed.deferred, 'a wait must not empty the pen')
  time.advance(1_200)
  assert.deepEqual(pen.release(routed.deferred, false), { kind: 'submit', deferred: routed.deferred })
  assert.equal(pen.pending, null)
})

test('the hold ceiling submits even while speech keeps arriving', () => {
  const time = clock()
  const pen = new DeferralPen(time.now)
  const routed = pen.offer('put that note in the')
  assert.equal(routed.kind, 'defer')
  if (routed.kind !== 'defer') return
  time.advance(DEFERRAL_MAX_HOLD_MS - 1)
  assert.deepEqual(pen.release(routed.deferred, true), { kind: 'wait' })
  time.advance(1)
  // A detector wedged in "speaking" must not be able to hold a turn forever.
  assert.deepEqual(pen.release(routed.deferred, true), { kind: 'submit', deferred: routed.deferred })
})

test('a stale release for a fragment someone else claimed reports gone', () => {
  const pen = new DeferralPen(clock().now)
  const routed = pen.offer('compare it with')
  assert.equal(routed.kind, 'defer')
  if (routed.kind !== 'defer') return
  assert.equal(pen.take(), routed.deferred)
  assert.deepEqual(pen.release(routed.deferred, false), { kind: 'gone' })
  // And a release for a fragment that was already submitted stays gone.
  assert.deepEqual(pen.release(routed.deferred, false), { kind: 'gone' })
})

test('take empties the pen and reports nothing when it was already empty', () => {
  const pen = new DeferralPen(clock().now)
  assert.equal(pen.take(), null)
  assert.equal(pen.offer('summarize the').kind, 'defer')
  assert.ok(pen.take())
  assert.equal(pen.pending, null)
  assert.equal(pen.take(), null)
})

test('a held fragment can be followed by a fresh deferral once it resolves', () => {
  const time = clock()
  const pen = new DeferralPen(time.now)
  const first = pen.offer('open the')
  assert.equal(first.kind, 'defer')
  if (first.kind !== 'defer') return
  assert.deepEqual(pen.release(first.deferred, false), { kind: 'submit', deferred: first.deferred })
  const second = pen.offer('and also send it to')
  assert.equal(second.kind, 'defer')
  assert.notEqual(pen.pending, first.deferred)
})
