import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

import {
  fitWindow, N_FRAMES, N_MELS, SMART_TURN_SAMPLE_RATE, SMART_TURN_SAMPLES, WhisperFeatures,
} from '../src/smartTurnFeatures.ts'

/**
 * The Smart Turn preprocessing chain, checked against the real thing.
 *
 * `smartTurnGolden.json` was produced by the actual HuggingFace
 * `WhisperFeatureExtractor(chunk_length=8)` and the actual ONNX model, from the
 * deterministic waveform rebuilt below (the generator is
 * `tools/smart_turn_golden.py`, which mirrors pipecat's `inference.py` call for
 * call, and is only re-run when the weights change). This matters more than
 * a normal unit test: every stage here is a silent-failure risk. A transposed
 * axis, an off-by-one frame, a non-periodic Hann window, or the wrong mel
 * normalization all produce a full-looking spectrogram and a plausible-looking
 * probability, and the only way to notice is to compare numbers with the
 * reference implementation.
 */

type Golden = {
  seconds: number
  shape: number[]
  mean: number
  std: number
  min: number
  max: number
  head: number[]
  stride37: number[]
  frame0: number[]
  frameLast: number[]
  probability: number
}

const golden: Record<string, Golden> = JSON.parse(
  readFileSync(new URL('./smartTurnGolden.json', import.meta.url), 'utf8'),
)

/**
 * Ported verbatim from `golden.py`, including the float32 cast at the end.
 *
 * The noise is an integer LCG rather than numpy's `randn` precisely so it can
 * live here as four lines of arithmetic: every intermediate stays under 2^53, so
 * float64 holds it exactly and Python and JavaScript produce identical draws.
 * Replaying a Mersenne Twister instead would have meant committing 3.6 MB of raw
 * numbers next to this file.
 */
function makeWaveform(samples: number): Float32Array {
  const out = new Float32Array(samples)
  let state = 1234567891
  for (let index = 0; index < samples; index++) {
    state = (state * 1664525 + 1013904223) % 4294967296
    const noise = 0.05 * ((state / 4294967296) * 2 - 1)
    const t = index / SMART_TURN_SAMPLE_RATE
    const sweep = Math.sin(2 * Math.PI * (120 + 900 * t) * t)
    const harmonic = 0.4 * Math.sin(2 * Math.PI * 1750 * t)
    const envelope = Math.min(1, t * 4) * Math.exp(-t / 2.5)
    out[index] = Math.fround((sweep + harmonic + noise) * envelope * 0.35)
  }
  return out
}

const close = (actual: number, expected: number, tolerance: number, what: string) => {
  assert.ok(
    Math.abs(actual - expected) <= tolerance,
    `${what}: expected ${expected}, got ${actual} (delta ${Math.abs(actual - expected)})`,
  )
}

test('the front-padding rule puts the speech at the END of the window', () => {
  // Not cosmetic. The model judges the boundary at the end of its input, so
  // padding the wrong side moves the thing being classified into the middle.
  const short = Float32Array.from([1, 2, 3])
  const fitted = fitWindow(short)
  assert.equal(fitted.length, SMART_TURN_SAMPLES)
  assert.equal(fitted[0], 0)
  assert.equal(fitted[SMART_TURN_SAMPLES - 4], 0)
  assert.deepEqual(Array.from(fitted.subarray(SMART_TURN_SAMPLES - 3)), [1, 2, 3])
  // Longer than the window keeps the tail, for the same reason.
  const long = Float32Array.from({ length: SMART_TURN_SAMPLES + 5 }, (_, index) => index)
  assert.equal(fitWindow(long).length, SMART_TURN_SAMPLES)
  assert.equal(fitWindow(long)[SMART_TURN_SAMPLES - 1], SMART_TURN_SAMPLES + 4)
})

for (const [label, expected] of Object.entries(golden)) {
  test(`log-mel features match the Python reference for ${label}`, () => {
    const features = new WhisperFeatures().compute(makeWaveform(Math.round(expected.seconds * SMART_TURN_SAMPLE_RATE)))
    assert.equal(features.length, N_MELS * N_FRAMES)
    assert.deepEqual(expected.shape, [N_MELS, N_FRAMES])

    let sum = 0
    let minimum = Infinity
    let maximum = -Infinity
    for (const value of features) {
      sum += value
      if (value < minimum) minimum = value
      if (value > maximum) maximum = value
    }
    const mean = sum / features.length
    let variance = 0
    for (const value of features) variance += (value - mean) ** 2
    const std = Math.sqrt(variance / features.length)

    close(mean, expected.mean, 2e-4, `${label} mean`)
    close(std, expected.std, 2e-4, `${label} std`)
    close(minimum, expected.min, 2e-4, `${label} min`)
    close(maximum, expected.max, 2e-4, `${label} max`)

    // Point comparisons catch what the aggregates cannot: a transposed grid has
    // the same mean and the same standard deviation as the correct one.
    expected.head.forEach((value, index) => close(features[index], value, 2e-3, `${label} head[${index}]`))
    expected.stride37.forEach((value, index) => {
      close(features[index * 37], value, 2e-3, `${label} stride37[${index}]`)
    })
    expected.frame0.forEach((value, mel) => {
      close(features[mel * N_FRAMES], value, 2e-3, `${label} frame0 mel ${mel}`)
    })
    expected.frameLast.forEach((value, mel) => {
      close(features[mel * N_FRAMES + N_FRAMES - 1], value, 2e-3, `${label} frameLast mel ${mel}`)
    })
  })
}

test('the output buffer is reused, so a caller that needs it must copy', () => {
  // Documented rather than incidental: allocating 256 KB per inference is what
  // turns a latency measurement into a measurement of the garbage collector.
  const extractor = new WhisperFeatures()
  const first = extractor.compute(makeWaveform(16_000))
  const second = extractor.compute(makeWaveform(48_000))
  assert.equal(first, second, 'the same Float32Array instance is returned')
})
