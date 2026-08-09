import assert from 'node:assert/strict'
import test from 'node:test'

import {
  FrameAssembler,
  frameRms,
  joinFrames,
  StreamingDownsampler,
  VAD_FRAME_MS,
  VAD_FRAME_SAMPLES,
} from '../src/audioFrames.ts'

test('the decimator averages the samples that fall in each output slot', () => {
  const resampler = new StreamingDownsampler(8, 4)
  assert.deepEqual([...resampler.push(new Float32Array([1, 1, -1, -1, 0, 0, 0, 0]))], [1, -1, 0, 0])
})

test('the decimator carries a partial output across block boundaries', () => {
  // A per-block decimator would round the boundary independently in every block
  // and drift over a long dictation; this one has to split 3:1 groups that land
  // across the seam. Blocks of 5 at 48 kHz are deliberately coprime with the ratio.
  const streamed = new StreamingDownsampler(48_000, 16_000)
  const source = Float32Array.from({ length: 30 }, (_value, index) => index)
  const pieces: number[] = []
  for (let offset = 0; offset < source.length; offset += 5) {
    pieces.push(...streamed.push(source.subarray(offset, offset + 5)))
  }
  const whole = new StreamingDownsampler(48_000, 16_000).push(source)
  assert.deepEqual(pieces, [...whole])
  assert.equal(pieces.length, 10)
  assert.equal(pieces[0], 1) // mean of 0, 1, 2
})

test('a context already at the target rate passes through untouched', () => {
  const resampler = new StreamingDownsampler(16_000, 16_000)
  assert.deepEqual([...resampler.push(new Float32Array([0.5, -0.5, 0.25]))], [0.5, -0.5, 0.25])
})

test('framing holds the remainder rather than emitting a short frame', () => {
  // Silero's output is meaningless on a frame that is not exactly 512 samples, so
  // a partial tail has to wait for the next block instead of being padded out.
  const assembler = new FrameAssembler(4)
  assert.deepEqual(assembler.push(new Float32Array([1, 2, 3])), [])
  const frames = assembler.push(new Float32Array([4, 5, 6, 7, 8]))
  assert.equal(frames.length, 2)
  assert.deepEqual([...frames[0]], [1, 2, 3, 4])
  assert.deepEqual([...frames[1]], [5, 6, 7, 8])
})

test('each frame is its own buffer, because frames are retained as utterance audio', () => {
  const assembler = new FrameAssembler(2)
  const frames = assembler.push(new Float32Array([1, 2, 3, 4]))
  assembler.push(new Float32Array([9, 9]))
  assert.deepEqual([...frames[0]], [1, 2])
  assert.deepEqual([...frames[1]], [3, 4])
})

test('frames rejoin in order and one frame is 32 ms', () => {
  assert.equal(VAD_FRAME_SAMPLES, 512)
  assert.equal(VAD_FRAME_MS, 32)
  const joined = joinFrames([new Float32Array([1, 2]), new Float32Array([3])])
  assert.deepEqual([...joined], [1, 2, 3])
  assert.deepEqual([...joinFrames([])], [])
})

test('frame energy is the root mean square, and silence is zero', () => {
  assert.equal(frameRms(new Float32Array([1, -1, 1, -1])), 1)
  assert.equal(frameRms(new Float32Array(8)), 0)
  assert.equal(frameRms(new Float32Array(0)), 0)
})
