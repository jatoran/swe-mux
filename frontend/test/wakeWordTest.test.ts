import assert from 'node:assert/strict'
import test from 'node:test'

import { buildVoiceMatcher, DEFAULT_COMMANDS, DEFAULT_WAKE_WORDS } from '../src/conversation.ts'
import { evaluateTrial, findWakeWord, summarizeTrials } from '../src/wakeWordTest.ts'
import type { WakeWordTrial } from '../src/wakeWordTest.ts'

const matcher = buildVoiceMatcher(DEFAULT_WAKE_WORDS, DEFAULT_COMMANDS)
const trial = (text: string, decodeMs = 100): WakeWordTrial =>
  evaluateTrial(text, matcher, DEFAULT_WAKE_WORDS, decodeMs)

test('a wake word is only heard as a whole word', () => {
  // The case the report exists to catch: a short trigger swallowed by a common
  // word. Counting "swe" inside "sweet" as a hit would claim the trigger survived
  // in exactly the situation that proves it did not.
  assert.equal(findWakeWord('sweet potato', ['swe']), null)
  assert.equal(findWakeWord('swe go', ['swe']), 'swe')
  assert.equal(findWakeWord('the mux terminal', DEFAULT_WAKE_WORDS), 'mux')
  assert.equal(findWakeWord('', DEFAULT_WAKE_WORDS), null)
  assert.equal(findWakeWord('anything', []), null)
})

test('the longest matching variant wins so a report names the right one', () => {
  assert.equal(findWakeWord('hey mucks send', ['mux', 'mucks']), 'mucks')
})

test('a trial keeps the raw transcript, not just a verdict', () => {
  // Choosing a trigger word means reading what came back, so the mis-hearings have
  // to survive scoring: "bucks" is the whole finding.
  const heard = trial('run the tests. Bucks send')
  assert.equal(heard.text, 'run the tests. Bucks send')
  assert.equal(heard.wake, null)
  assert.equal(heard.command, null)
})

test('a matched utterance reports the action the live matcher fired', () => {
  const heard = trial('run the tests. Mux send')
  assert.equal(heard.command, 'send')
  assert.equal(heard.wake, 'mux')
})

test('hearing the wake word but not the phrase is a distinct outcome', () => {
  // Different problem, different fix: the trigger is fine and the phrase list is
  // what needs the extra wording.
  const heard = trial('Mux fire away')
  assert.equal(heard.wake, 'mux')
  assert.equal(heard.command, null)
  const report = summarizeTrials([heard])
  assert.deepEqual([report.wakeHeard, report.matched, report.misses.length], [1, 0, 0])
})

test('nothing recognized is recorded as a trial, not discarded', () => {
  // Silence back from the decoder is the strongest possible evidence against a
  // trigger word; dropping it would bias the report toward whatever did decode.
  const report = summarizeTrials([trial(''), trial('Mux send')])
  assert.equal(report.total, 2)
  assert.equal(report.matched, 1)
  assert.deepEqual(report.misses, [''])
})

test('the summary counts each action and reports the median decode', () => {
  const report = summarizeTrials([
    trial('Mux send', 90),
    trial('Mux send', 110),
    trial('Mux cancel that', 130),
    trial('Bucks send', 400),
  ])
  assert.equal(report.total, 4)
  assert.equal(report.matched, 3)
  assert.equal(report.wakeHeard, 3)
  assert.deepEqual(report.byCommand, [{ command: 'send', count: 2 }, { command: 'cancel', count: 1 }])
  assert.deepEqual(report.misses, ['Bucks send'])
  assert.equal(report.medianDecodeMs, 110)
})

test('an empty run summarizes to zeroes rather than dividing by nothing', () => {
  assert.deepEqual(summarizeTrials([]), {
    total: 0, matched: 0, wakeHeard: 0, byCommand: [], misses: [], medianDecodeMs: 0,
  })
})
