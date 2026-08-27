import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import {
  describeReadiness, explainReason, freshestReadiness, readinessAgeLabel, readinessReasons,
  wordReasons,
} from '../src/deliveryReadiness.ts'
import type { DeliveryReadiness } from '../src/types.ts'

const src = (name: string) => readFileSync(join(import.meta.dirname, '..', 'src', name), 'utf8')

const reading = (patch: Partial<DeliveryReadiness> = {}): DeliveryReadiness => ({
  state: 'blocked',
  reason: 'terminal_input_after_completion',
  reasons: ['terminal_input_after_completion'],
  protected: [],
  observed_at: 1000,
  authorized: false,
  ...patch,
})

test('the reason a person actually hits is explained, not printed as a code', () => {
  // The report this came from: "it says terminal_input_after_completion even though
  // that session is ready, cuz i had had input in there but i have since deleted it."
  const explained = explainReason('terminal_input_after_completion')
  assert.match(explained.summary, /typed in this terminal after its last turn ended/)
  // The half that answers the actual confusion: deleting the text does not clear it.
  assert.match(String(explained.clears), /Clearing the line does not clear this/)
  assert.match(String(explained.clears), /next turn ends/)
})

test('an unmapped reason survives as itself rather than disappearing', () => {
  // The vocabulary is the daemon's and it grows. A reader shown a code they can
  // search for is mildly unhelped; a reader shown nothing is misled.
  assert.equal(explainReason('some_future_reason').summary, 'some future reason')
  assert.equal(explainReason('some_future_reason').clears, undefined)
})

test('every reason the tracker can emit has a sentence', () => {
  // Read out of the classifier rather than listed here, so a new reason code lands
  // as a failing test instead of as a raw string in front of a user.
  const classifier = readFileSync(
    join(import.meta.dirname, '..', '..', 'src', 'swe_mux', 'delivery_readiness.py'),
    'utf8',
  )
  const emitted = new Set<string>()
  for (const match of classifier.matchAll(/(?:hard_block_reasons|unknown_reasons|reasons)\s*=?\.?append\(\s*"([a-z_]+)"/g)) emitted.add(match[1])
  for (const match of classifier.matchAll(/reasons = \["([a-z_]+)"\]/g)) emitted.add(match[1])
  assert.ok(emitted.size > 8, `expected to find the reason vocabulary, found ${emitted.size}`)
  const module = src('deliveryReadiness.ts')
  const unmapped = [...emitted].filter(reason => !module.includes(`  ${reason}: {`))
  assert.deepEqual(unmapped, [])
})

test('a verdict says what it is, whether it can be overridden, and how old it is', () => {
  const verdict = describeReadiness(reading(), 1042)
  assert.ok(verdict)
  assert.equal(verdict.state, 'blocked')
  assert.equal(verdict.headline, 'not deliverable')
  assert.match(verdict.summary, /typed in this terminal/)
  assert.equal(verdict.protected, false)
  assert.equal(verdict.ageSeconds, 42)
  assert.equal(readinessAgeLabel(verdict), '42s ago')
})

test('a protection is named before the press, not after it', () => {
  const verdict = describeReadiness(
    reading({ reason: 'approval_required', reasons: ['approval_required'], protected: ['awaiting_approval'] }),
  )
  assert.ok(verdict)
  assert.equal(verdict.protected, true)
})

test('a fresh reading shows no age at all', () => {
  const verdict = describeReadiness(reading(), 1002)
  assert.ok(verdict)
  assert.equal(readinessAgeLabel(verdict), '')
})

test('an absent reading is not the same as an unknown verdict', () => {
  // "the daemon has not told us" and "the daemon evaluated this as unknown" are
  // different facts, and only the second is a readiness verdict.
  assert.equal(describeReadiness(undefined), null)
  assert.equal(describeReadiness(null), null)
  assert.equal(describeReadiness(reading({ state: 'unknown' }))?.headline, 'readiness unknown')
})

test('a safe verdict carries no reason clause', () => {
  const verdict = describeReadiness(
    reading({ state: 'safe', reason: 'all_required_evidence_positive', reasons: ['all_required_evidence_positive'] }),
  )
  assert.ok(verdict)
  assert.equal(verdict.headline, 'deliverable')
  assert.equal(verdict.summary, '')
  assert.equal(verdict.clears, '')
})

test('every reason is carried, not just the leading one', () => {
  const verdict = describeReadiness(
    reading({ reasons: ['root_agent_working', 'operator_recently_typed'] }),
  )
  assert.ok(verdict)
  assert.match(verdict.summary, /mid-turn/)
  assert.equal(verdict.also.length, 1)
  assert.match(verdict.also[0], /typing in this terminal right now/)
})

test('a payload with no reasons list still reports its single reason', () => {
  // Tolerating the shape that shipped before `reasons` existed: a client running
  // against an older daemon must degrade to one reason, not to none.
  const older = { state: 'blocked', reason: 'session_ended', authorized: false } as DeliveryReadiness
  assert.deepEqual(readinessReasons(older), ['session_ended'])
  assert.match(String(describeReadiness(older)?.summary), /has ended/)
})

test('the newest of several readings wins, and an unstamped one loses', () => {
  const older = reading({ observed_at: 10, reason: 'root_agent_working' })
  const newer = reading({ observed_at: 20, reason: 'session_ended' })
  assert.equal(freshestReadiness(older, newer)?.reason, 'session_ended')
  assert.equal(freshestReadiness(newer, older)?.reason, 'session_ended')
  // An unstamped payload predates the stamp, so it is older by construction.
  const unstamped = { state: 'safe', reason: 'x', authorized: false } as DeliveryReadiness
  assert.equal(freshestReadiness(unstamped, older)?.reason, 'root_agent_working')
  assert.equal(freshestReadiness(undefined, null), undefined)
})

test('every surface that prints refusal reasons goes through one wording', () => {
  // Four places used to `join(', ')` the raw codes independently, which is four
  // places for the vocabulary to drift.
  assert.match(wordReasons(['session_ended']), /has ended/)
  for (const name of ['QueuePane.tsx', 'SendToAgentPicker.tsx', 'PromptsTab.tsx']) {
    const module = src(name)
    assert.ok(module.includes('wordReasons'), `${name} should word its reasons`)
    assert.ok(
      !/result\.reasons\.join|reasons\.join\(', '\)/.test(module.replace(/title=\{[^}]*\}/g, '')),
      `${name} still prints raw reason codes`,
    )
  }
})

test('readiness is advisory and never disables the send', () => {
  // A stale advisory that removes the operator's only override would be a false
  // block with no way out, which is strictly worse than a wrong label. The confirm
  // is always the second press against a refusal the daemon issued that instant.
  const queuePane = src('QueuePane.tsx')
  const start = queuePane.indexOf('class={`queue-send${sendHint')
  assert.ok(start > 0, 'the resting send button should carry the hint class')
  // Searched forward from the button, not from the top of the file: the phrase also
  // appears in the schedule copy above it, and slicing to the first match yielded an
  // empty string that every assertion below passed vacuously.
  const sendButton = queuePane.slice(start, queuePane.indexOf('Send now', start))
  assert.match(sendButton, /disabled=\{busy\}/)
  assert.ok(!sendButton.includes('verdict'), 'the send button must not gate on the advisory')
})

test('the composer estimate narrates the block but never clears it', () => {
  // `unsent_input` is deliberately not an input to readiness: an estimate that
  // concluded "empty" would authorize a send on top of text nothing can see. It is
  // allowed to explain why the block looks wrong, and nothing more.
  const queuePane = src('QueuePane.tsx')
  assert.match(queuePane, /!session\?\.unsent_input/)
  assert.match(queuePane, /Nothing is sitting in the composer now/)
  // It never participates in deciding what to show as the verdict.
  const module = src('deliveryReadiness.ts')
  assert.ok(!module.includes('unsent_input'))
})
