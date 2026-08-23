import assert from 'node:assert/strict'
import test from 'node:test'

import {
  ASSISTANT_HOLD_COMPLETION, DEFERRAL_MAX_HOLD_MS, DEFERRAL_PARK_MAX_MS,
  DEFERRAL_PARK_MAX_WORDS, DeferralPen,
} from '../src/utteranceDeferral.ts'
import { COMPLETION, DEFAULT_CHAT_PATIENCE_MS, deferralExtensionMs } from '../src/utteranceCompleteness.ts'

/** Every `offer` needs a patience now; none of these tests vary it. */
const PATIENCE = DEFAULT_CHAT_PATIENCE_MS

/** A hand-cranked clock, so the hold ceiling is testable without waiting 15 s. */
const clock = () => {
  let value = 0
  return { now: () => value, advance: (ms: number) => { value += ms } }
}

test('a finished utterance dispatches untouched and holds nothing', () => {
  const pen = new DeferralPen(clock().now)
  const routed = pen.offer('  open the alpha session  ', PATIENCE)
  assert.deepEqual(routed, { kind: 'dispatch', text: 'open the alpha session', merged: null })
  assert.equal(pen.pending, null)
})

test('an unfinished utterance is held with the trigger that caused it', () => {
  const time = clock()
  time.advance(4_000)
  const pen = new DeferralPen(time.now)
  const routed = pen.offer('open the alpha session and', PATIENCE)
  assert.equal(routed.kind, 'defer')
  if (routed.kind !== 'defer') return
  assert.deepEqual(routed.deferred, {
    text: 'open the alpha session and',
    trigger: 'and',
    kind: 'conjunction',
    words: 5,
    at: 4_000,
    source: 'heuristic',
    completion: COMPLETION.conjunction,
    extensionMs: deferralExtensionMs(PATIENCE, COMPLETION.conjunction),
  })
  assert.equal(pen.pending, routed.deferred)
})

test('the window a deferral grants tracks the score that caused it', () => {
  const pen = () => new DeferralPen(clock().now)
  const held = (text: string) => {
    const routed = pen().offer(text, PATIENCE)
    assert.equal(routed.kind, 'defer', `expected "${text}" to defer`)
    return routed.kind === 'defer' ? routed.deferred.extensionMs : 0
  }
  // "the" cannot end a sentence; "about" often can. The pen must not make the
  // operator pay the same silence for both.
  assert.ok(held('put that note in the') > held('tell me what you think about'))
})

test('the assistant parks a fragment silently, and a park never submits itself', () => {
  const time = clock()
  const pen = new DeferralPen(time.now)
  const parked = pen.park('now I want you to add', PATIENCE)
  assert.ok(parked, 'the model asked to hold, so something must be held')
  if (!parked) return
  assert.equal(parked.source, 'assistant')
  assert.equal(parked.completion, ASSISTANT_HOLD_COMPLETION)
  // Sized like any other hold: the gate reads this, and an operator the model
  // just judged mid-thought needs the roomier tail most.
  assert.ok(parked.extensionMs > 0)
  assert.equal(pen.pending, parked)
  // The next breath joins it and the whole thought goes as one turn. This is the
  // only thing that resolves a park, which is what makes a hold loop impossible:
  // the text is never re-sent alone for the model to hold a second time.
  const merged = pen.offer('a note about the queue', PATIENCE)
  assert.deepEqual(merged, {
    kind: 'dispatch',
    text: 'now I want you to add a note about the queue',
    merged: parked,
  })
  assert.equal(pen.pending, null)
})

test('a park refuses to stack, to swallow a turn-sized fragment, or to hold nothing', () => {
  const pen = new DeferralPen(clock().now)
  assert.equal(pen.park('   ', PATIENCE), null, 'empty text is not a fragment')
  assert.equal(pen.park('x '.repeat(DEFERRAL_PARK_MAX_WORDS), PATIENCE), null,
    'past the word ceiling the accumulated text is a turn in its own right')
  const first = pen.park('and I was thinking we could', PATIENCE)
  assert.ok(first)
  // One fragment, always. A second park would break the merge into a chain.
  assert.equal(pen.park('something else entirely', PATIENCE), null)
  assert.equal(pen.pending, first)
})

test('an abandoned park expires instead of gluing itself to a later sentence', () => {
  const time = clock()
  const pen = new DeferralPen(time.now)
  const parked = pen.park('so the thing I wanted was', PATIENCE)
  assert.ok(parked)
  time.advance(DEFERRAL_PARK_MAX_MS - 1)
  assert.equal(pen.expireStalePark(), null, 'still fresh, still waiting for the rest')
  assert.equal(pen.pending, parked)
  time.advance(1)
  assert.equal(pen.expireStalePark(), parked)
  assert.equal(pen.pending, null)
  assert.equal(pen.expireStalePark(), null, 'nothing held, nothing to expire')
})

test('expiry never touches a heuristic deferral, which has its own release path', () => {
  const time = clock()
  const pen = new DeferralPen(time.now)
  assert.equal(pen.offer('open the', PATIENCE).kind, 'defer')
  time.advance(DEFERRAL_PARK_MAX_MS * 2)
  assert.equal(pen.expireStalePark(), null)
  assert.ok(pen.pending, 'the release timer owns this one, not the park sweep')
})

test('the second breath merges into the held fragment and dispatches as one turn', () => {
  const pen = new DeferralPen(clock().now)
  const first = pen.offer('open the alpha session and', PATIENCE)
  assert.equal(first.kind, 'defer')
  const second = pen.offer('tell me what it is running', PATIENCE)
  assert.deepEqual(second, {
    kind: 'dispatch',
    text: 'open the alpha session and tell me what it is running',
    merged: first.kind === 'defer' ? first.deferred : null,
  })
  assert.equal(pen.pending, null)
})

test('a merge is never re-judged, so fragments cannot compound into an unbounded wait', () => {
  const pen = new DeferralPen(clock().now)
  assert.equal(pen.offer('I want to look at the queue and', PATIENCE).kind, 'defer')
  // The continuation is itself unfinished. It still dispatches: at most one
  // deferral per utterance is the whole bound, and re-judging here is exactly
  // how a chain of breaths would hold a turn forever.
  const second = pen.offer('then the transcript for', PATIENCE)
  assert.equal(second.kind, 'dispatch')
  assert.equal(second.kind === 'dispatch' && second.text, 'I want to look at the queue and then the transcript for')
  assert.equal(pen.pending, null)
})

test('the release waits while the operator is still mid-thought, then submits', () => {
  const time = clock()
  const pen = new DeferralPen(time.now)
  const routed = pen.offer('send it to', PATIENCE)
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
  const routed = pen.offer('put that note in the', PATIENCE)
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
  const routed = pen.offer('compare it with', PATIENCE)
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
  assert.equal(pen.offer('summarize the', PATIENCE).kind, 'defer')
  assert.ok(pen.take())
  assert.equal(pen.pending, null)
  assert.equal(pen.take(), null)
})

test('a held fragment can be followed by a fresh deferral once it resolves', () => {
  const time = clock()
  const pen = new DeferralPen(time.now)
  const first = pen.offer('open the', PATIENCE)
  assert.equal(first.kind, 'defer')
  if (first.kind !== 'defer') return
  assert.deepEqual(pen.release(first.deferred, false), { kind: 'submit', deferred: first.deferred })
  const second = pen.offer('and also send it to', PATIENCE)
  assert.equal(second.kind, 'defer')
  assert.notEqual(pen.pending, first.deferred)
})
