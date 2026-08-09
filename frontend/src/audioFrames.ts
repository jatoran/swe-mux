/**
 * Turning a microphone stream into the fixed-size 16 kHz frames a VAD wants.
 *
 * Silero only accepts exactly 512 samples at 16 kHz (32 ms), and the detector's
 * state machine counts frames rather than milliseconds so its timing cannot drift
 * when inference falls a block behind. Both capture sources — the AudioWorklet and
 * the ScriptProcessorNode fallback — feed these same two stages, so there is one
 * resampler and one framing rule to be wrong about instead of two.
 */

export const VAD_SAMPLE_RATE = 16_000
/** Silero v5 accepts this frame length and no other. 512 / 16 kHz = 32 ms. */
export const VAD_FRAME_SAMPLES = 512
export const VAD_FRAME_MS = (VAD_FRAME_SAMPLES / VAD_SAMPLE_RATE) * 1000

/**
 * Averaging decimator that carries its partial output across blocks.
 *
 * The stateless `downsample` used per-utterance cannot be reused here: audio
 * arrives in blocks whose length is unrelated to the resample ratio, so a
 * per-block decimator would round the boundary independently in every block and
 * accumulate drift over a long dictation.
 */
export class StreamingDownsampler {
  private readonly ratio: number
  private inputIndex = 0
  private outputIndex = 0
  private sum = 0
  private count = 0

  constructor(inputRate: number, outputRate: number = VAD_SAMPLE_RATE) {
    this.ratio = Math.max(1, inputRate / outputRate)
  }

  push(block: Float32Array): Float32Array {
    // Upper bound; the exact count depends on where the block ends relative to
    // the next output boundary, so the result is sliced to what actually landed.
    const out = new Float32Array(Math.ceil(block.length / this.ratio) + 1)
    let produced = 0
    for (let index = 0; index < block.length; index++) {
      this.sum += block[index]
      this.count++
      this.inputIndex++
      if (this.inputIndex >= Math.round((this.outputIndex + 1) * this.ratio)) {
        out[produced++] = this.count ? this.sum / this.count : 0
        this.outputIndex++
        this.sum = 0
        this.count = 0
      }
    }
    return out.subarray(0, produced)
  }

  reset(): void {
    this.inputIndex = 0
    this.outputIndex = 0
    this.sum = 0
    this.count = 0
  }
}

/** Cuts a sample stream into fixed-length frames, holding the remainder. */
export class FrameAssembler {
  private readonly size: number
  private pending: Float32Array
  private filled = 0

  constructor(size: number = VAD_FRAME_SAMPLES) {
    this.size = size
    this.pending = new Float32Array(size)
  }

  push(samples: Float32Array): Float32Array[] {
    const frames: Float32Array[] = []
    let offset = 0
    while (offset < samples.length) {
      const take = Math.min(this.size - this.filled, samples.length - offset)
      this.pending.set(samples.subarray(offset, offset + take), this.filled)
      this.filled += take
      offset += take
      if (this.filled === this.size) {
        // A fresh copy every time: frames are retained for the utterance audio,
        // so handing out a reused buffer would rewrite already-captured speech.
        frames.push(this.pending.slice())
        this.filled = 0
      }
    }
    return frames
  }

  reset(): void {
    this.filled = 0
  }
}

/** Concatenate captured frames into one contiguous buffer for encoding. */
export function joinFrames(frames: Float32Array[]): Float32Array {
  const total = frames.reduce((sum, frame) => sum + frame.length, 0)
  const joined = new Float32Array(total)
  let offset = 0
  for (const frame of frames) {
    joined.set(frame, offset)
    offset += frame.length
  }
  return joined
}

/** Root-mean-square of one frame, for the energy detector fallback. */
export function frameRms(frame: Float32Array): number {
  let squareSum = 0
  for (const sample of frame) squareSum += sample * sample
  return Math.sqrt(squareSum / Math.max(1, frame.length))
}
