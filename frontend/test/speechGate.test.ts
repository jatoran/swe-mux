import assert from 'node:assert/strict'
import test from 'node:test'

import { VAD_FRAME_MS } from '../src/audioFrames.ts'
import { ENERGY_GATE, SILERO_GATE, SpeechGate } from '../src/speechGate.ts'
import type { GateEvent } from '../src/speechGate.ts'

const feed = (gate: SpeechGate, probability: number, frames: number): GateEvent[] => {
  const events: GateEvent[] = []
  for (let index = 0; index < frames; index++) events.push(...gate.push(probability))
  return events
}
const frames = (ms: number) => Math.ceil(ms / VAD_FRAME_MS)
const kinds = (events: GateEvent[]) => events.map(event => event.type)

test('silence before speech produces nothing at all', () => {
  const gate = new SpeechGate(SILERO_GATE)
  assert.deepEqual(feed(gate, 0.1, 50), [])
  assert.equal(gate.speaking, false)
})

test('Silero ends an utterance after 352 ms, not 900', () => {
  const gate = new SpeechGate(SILERO_GATE)
  assert.deepEqual(kinds(feed(gate, 0.9, frames(SILERO_GATE.minSpeechMs) + 1)), ['speech-start'])
  // One frame short of the endpoint: speculation has fired, the endpoint has not.
  const before = kinds(feed(gate, 0.05, 10))
  assert.deepEqual(before, ['speculate'])
  assert.deepEqual(kinds(gate.push(0.05)), ['endpoint'])
  assert.equal(gate.silenceMs, SILERO_GATE.endpointSilenceMs)
})

test('a speculative decode is asked for well before the endpoint', () => {
  const gate = new SpeechGate(SILERO_GATE)
  feed(gate, 0.9, frames(SILERO_GATE.minSpeechMs) + 1)
  assert.deepEqual(kinds(feed(gate, 0.05, 4)), [])
  assert.deepEqual(kinds(gate.push(0.05)), ['speculate'])
  assert.equal(gate.speculating, true)
  // The whole point of the early start: the decode has to be able to finish before
  // the endpoint, or the grammar short-circuit can never skip any of the tail.
  assert.ok(SILERO_GATE.speculativeSilenceMs * 2 < SILERO_GATE.endpointSilenceMs)
})

test('speech resuming voids the speculation exactly once', () => {
  const gate = new SpeechGate(SILERO_GATE)
  feed(gate, 0.9, frames(SILERO_GATE.minSpeechMs) + 1)
  feed(gate, 0.05, 5)
  assert.equal(gate.speculating, true)
  assert.deepEqual(kinds(gate.push(0.9)), ['resume'])
  assert.equal(gate.speculating, false)
  assert.equal(gate.silenceMs, 0)
  // Already void: continuing to speak must not keep re-announcing it.
  assert.deepEqual(kinds(feed(gate, 0.9, 5)), [])
  // A second pause speculates again, because it is a new opportunity.
  assert.deepEqual(kinds(feed(gate, 0.05, 5)), ['speculate'])
})

test('extra tail patience delays the endpoint but never the speculation', () => {
  // Chat patience: thinking out loud gets a longer window before it becomes an
  // assistant turn, while the speculative decode still fires at its own
  // threshold so a wake-worded command can short-circuit the longer tail.
  let patience = 1200
  const gate = new SpeechGate(SILERO_GATE, () => patience)
  assert.deepEqual(kinds(feed(gate, 0.9, frames(SILERO_GATE.minSpeechMs) + 1)), ['speech-start'])
  assert.deepEqual(kinds(feed(gate, 0.05, frames(SILERO_GATE.speculativeSilenceMs))), ['speculate'])
  // The default endpoint passes with no event; the patient one fires on time.
  assert.deepEqual(kinds(feed(gate, 0.05, frames(SILERO_GATE.endpointSilenceMs))), [])
  assert.deepEqual(kinds(feed(gate, 0.05, frames(1200) + 1)), ['endpoint'])
  // Consulted per frame: dropping the patience takes effect immediately.
  patience = 0
  const fast = new SpeechGate(SILERO_GATE, () => patience)
  feed(fast, 0.9, frames(SILERO_GATE.minSpeechMs) + 1)
  const events = kinds(feed(fast, 0.05, frames(SILERO_GATE.endpointSilenceMs) + 1))
  assert.ok(events.includes('endpoint'))
})

test('hysteresis keeps a marginal frame from being read as a pause', () => {
  // Entry needs 0.5, but staying in speech only needs 0.35: without the gap, a
  // probability hovering at the threshold would flap the silence counter and end
  // the utterance in the middle of a word.
  const gate = new SpeechGate(SILERO_GATE)
  feed(gate, 0.9, frames(SILERO_GATE.minSpeechMs) + 1)
  feed(gate, 0.4, 20)
  assert.equal(gate.speaking, true)
  assert.equal(gate.silenceMs, 0)
})

test('a cough is not an utterance', () => {
  const gate = new SpeechGate(SILERO_GATE)
  assert.deepEqual(kinds(gate.push(0.9)), ['speech-start'])
  assert.deepEqual(kinds(feed(gate, 0.05, 60)), [])
  assert.equal(gate.speaking, true)
})

test('the hard cap ends a monologue even while it is still speech', () => {
  const gate = new SpeechGate(SILERO_GATE)
  const cap = Math.ceil(SILERO_GATE.maxUtteranceMs / VAD_FRAME_MS)
  assert.deepEqual(kinds(feed(gate, 0.9, cap - 1)), ['speech-start'])
  const capped = gate.push(0.9)
  assert.deepEqual(capped, [{ type: 'endpoint', reason: 'cap' }])
})

test('the energy fallback keeps its long tail and never speculates', () => {
  const gate = new SpeechGate(ENERGY_GATE)
  feed(gate, 1, frames(ENERGY_GATE.minSpeechMs) + 1)
  // An RMS detector false-triggers on breath, so a short tail would cut words in
  // half; the tail is the price of the detector, not a preference.
  assert.deepEqual(kinds(feed(gate, 0, frames(ENERGY_GATE.endpointSilenceMs) - 1)), [])
  assert.deepEqual(kinds(gate.push(0)), ['endpoint'])
  assert.equal(ENERGY_GATE.speculativeSilenceMs, 0)
})

test('reset returns the gate to its pre-speech state', () => {
  const gate = new SpeechGate(SILERO_GATE)
  feed(gate, 0.9, 20)
  gate.reset()
  assert.equal(gate.speaking, false)
  assert.equal(gate.silenceMs, 0)
  assert.equal(gate.speechMs, 0)
  assert.equal(gate.speculating, false)
})
