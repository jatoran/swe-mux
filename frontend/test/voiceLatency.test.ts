import assert from 'node:assert/strict'
import test from 'node:test'

import { buildLatencySample, formatLatency, latencyStages } from '../src/voiceLatency.ts'
import type { CaptureMarks } from '../src/voiceLatency.ts'

const marks = (overrides: Partial<CaptureMarks> = {}): CaptureMarks => ({
  utteranceId: 'utterance-1',
  speechEndAt: 1_000,
  finishedAt: 1_900,
  encodedAt: 1_912,
  audioMs: 1_600,
  ...overrides,
})

test('the trailing-silence wait is charged to the utterance, not hidden', () => {
  // The endpoint fires 900 ms after speech stopped. Dating the sample from the
  // endpoint instead would report a fast pipeline that feels slow.
  const sample = buildLatencySample({
    marks: marks(),
    postAt: 1_915,
    responseAt: 2_215,
    actionAt: 2_218,
    server: { queue_ms: 40, decode_ms: 240, server_ms: 290, audio_ms: 1_600 },
  })
  assert.equal(sample.endpoint_ms, 900)
  assert.equal(sample.encode_ms, 12)
  assert.equal(sample.wait_ms, 3)
  assert.equal(sample.total_ms, 1_218)
  assert.equal(latencyStages(sample).to_post_ms, 915)
})

test('everything after the decode is charged to text → action', () => {
  // The daemon spends 10 ms after the decode building its response; the text
  // already exists by then, so that time belongs to the last stage rather than
  // vanishing between the daemon's `server_ms` and the client's clock.
  const sample = buildLatencySample({
    marks: marks(),
    postAt: 1_915,
    responseAt: 2_215,
    actionAt: 2_218,
    server: { queue_ms: 40, decode_ms: 240, server_ms: 290 },
  })
  assert.equal(sample.action_ms, 13)
})

test('transport is the round trip minus the daemon time, never negative', () => {
  const sample = buildLatencySample({
    marks: marks(),
    postAt: 1_912,
    responseAt: 2_212,
    server: { queue_ms: 40, decode_ms: 250, server_ms: 295 },
    actionAt: 2_213,
  })
  assert.equal(sample.upload_ms, 5)
  assert.equal(latencyStages(sample).to_decode_ms, 45)

  // Two different clocks and rounding can put a loopback request under zero; the
  // stage floors at zero rather than reporting an impossible negative transport.
  const tight = buildLatencySample({
    marks: marks(),
    postAt: 1_912,
    responseAt: 2_212,
    server: { server_ms: 305, queue_ms: 40, decode_ms: 250 },
    actionAt: 2_213,
  })
  assert.equal(tight.upload_ms, 0)
})

test('a missing timings block degrades to client-only stages', () => {
  // An older daemon (or a frozen bundle mid-update) answers without timings. The
  // sample must still be usable rather than full of NaN.
  const sample = buildLatencySample({
    marks: marks(),
    postAt: 1_912,
    responseAt: 2_212,
    actionAt: 2_215,
    server: {},
  })
  assert.equal(sample.queue_ms, 0)
  assert.equal(sample.decode_ms, 0)
  assert.equal(sample.upload_ms, 300)
  assert.equal(sample.action_ms, 3)
  assert.equal(sample.audio_ms, 1_600)
  assert.ok(Object.values(sample).every(value => typeof value !== 'number' || Number.isFinite(value)))
})

test('the four stages sum to the total when nothing is unaccounted for', () => {
  const sample = buildLatencySample({
    marks: marks(),
    postAt: 1_915,
    responseAt: 2_215,
    actionAt: 2_218,
    server: { queue_ms: 40, decode_ms: 240, server_ms: 290 },
  })
  const stages = latencyStages(sample)
  const summed = stages.to_post_ms + stages.to_decode_ms + stages.decode_ms + stages.action_ms
  assert.equal(summed, sample.total_ms)
})

test('the matched command is carried so command and dictation totals separate', () => {
  const command = buildLatencySample({
    marks: marks(), postAt: 1_912, responseAt: 2_112, actionAt: 2_113, server: {}, command: 'send',
  })
  const dictation = buildLatencySample({
    marks: marks(), postAt: 1_912, responseAt: 2_112, actionAt: 2_113, server: {},
  })
  assert.equal(command.command, 'send')
  assert.equal(dictation.command, '')
})

test('the compact line names every stage', () => {
  const line = formatLatency(buildLatencySample({
    marks: marks(),
    postAt: 1_915,
    responseAt: 2_215,
    actionAt: 2_218,
    server: { queue_ms: 40, decode_ms: 240, server_ms: 290 },
  }))
  assert.match(line, /^1218 ms — endpoint 915 · send 50 · decode 240 · act 13$/)
})
