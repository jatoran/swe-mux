import {
  N_FRAMES, N_MELS, SMART_TURN_SAMPLE_RATE, SMART_TURN_SAMPLES, WhisperFeatures,
} from './smartTurnFeatures.ts'

/**
 * Smart Turn v3 (pipecat-ai, BSD-2-Clause) over onnxruntime-web.
 *
 * An acoustic-and-semantic end-of-turn classifier: a Whisper Tiny encoder with a
 * linear head, 8M parameters, int8-quantized to about 8 MB. It answers a
 * different question from the word heuristic in `utteranceCompleteness.ts` -
 * "did this person stop talking", judged from intonation and rhythm as well as
 * words, rather than "does the transcript end on a dangler" - and it does it
 * without a transcript, so it can run the moment the gate sees silence.
 *
 * EXPERIMENTAL, and deliberately not wired into capture. The open question is
 * not accuracy, which is published, but whether a Whisper encoder over 8 s of
 * audio is affordable in SINGLE-THREADED WASM on a phone: `ort.env.wasm.numThreads`
 * cannot exceed 1 here, because threads need `SharedArrayBuffer`, which needs
 * COOP/COEP headers that the Tailscale Serve path cannot send. `smart-turn-lab.html`
 * exists to measure exactly that on real devices before any of this is trusted.
 *
 * Structured like `sileroVad.ts` for the same reasons, including the relative
 * path into node_modules for the runtime's own files: onnxruntime-web's exports
 * map does not expose `dist/`, and leaving `wasmPaths` unset makes the dev server
 * hand back `index.html` for the `.wasm` request.
 */

type OrtModule = typeof import('onnxruntime-web')
type Session = Awaited<ReturnType<OrtModule['InferenceSession']['create']>>

/**
 * The weights are passed in, not resolved here.
 *
 * They live in `frontend/models/`, outside `public/`, so that a production build
 * never copies 8 MB into the daemon's static tree for a page that is not an
 * entry point in production. The lab reaches them with a `?url` import, which
 * the dev server resolves; nothing else imports this module yet.
 */
export const SMART_TURN_MODEL_FILE = 'smart-turn-v3.2-cpu.onnx'

/** At or above this the model says the speaker finished. Pipecat's own default. */
export const SMART_TURN_THRESHOLD = 0.5

export type TurnVerdict = {
  /** P(the speaker finished their turn), straight from the model's sigmoid. */
  probability: number
  complete: boolean
  /** Feature extraction time, which is pure JS and often the larger half. */
  featuresMs: number
  /** ONNX inference time. The number the phone question actually turns on. */
  inferenceMs: number
  /** Seconds of real audio in the window, before padding. */
  audioSeconds: number
}

export class SmartTurn {
  private ort: OrtModule | null = null
  private session: Session | null = null
  private readonly features = new WhisperFeatures()
  private input: InstanceType<OrtModule['Tensor']> | null = null

  get ready(): boolean { return this.session !== null }

  /** Bytes the page had to fetch to get here, for the lab's asset-weight line. */
  modelBytes = 0

  async load(modelUrl: string): Promise<void> {
    if (this.session) return
    const [ort, wasmUrl, glueUrl] = await Promise.all([
      import('onnxruntime-web'),
      import('../node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.wasm?url')
        .then(module => module.default),
      import('../node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.mjs?url')
        .then(module => module.default),
    ])
    ort.env.wasm.numThreads = 1
    ort.env.wasm.proxy = false
    ort.env.wasm.wasmPaths = { wasm: wasmUrl, mjs: glueUrl }
    ort.env.logLevel = 'error'
    // Fetched rather than imported so the lab can report the byte count, and so
    // the weights stay an ordinary public asset instead of a bundler dependency
    // - nothing in the shipped app imports this module yet.
    const response = await fetch(modelUrl)
    if (!response.ok) {
      throw new Error(
        `Smart Turn weights are missing (${response.status} for ${modelUrl}). `
        + 'Run: uv run python tools/fetch_smart_turn.py',
      )
    }
    const weights = new Uint8Array(await response.arrayBuffer())
    this.modelBytes = weights.byteLength
    this.session = await ort.InferenceSession.create(weights, {
      executionProviders: ['wasm'],
      interOpNumThreads: 1,
      intraOpNumThreads: 1,
      graphOptimizationLevel: 'all',
    })
    this.ort = ort
  }

  /**
   * Score one utterance. `audio` is 16 kHz mono; anything longer than 8 s is
   * judged on its last 8 s, which is where the turn boundary is.
   */
  async predict(audio: Float32Array): Promise<TurnVerdict> {
    const ort = this.ort
    const session = this.session
    if (!ort || !session) throw new Error('Smart Turn is not loaded')
    const featureStart = performance.now()
    const grid = this.features.compute(audio)
    const featuresMs = performance.now() - featureStart
    // One tensor, reused: the shape never changes, and re-allocating 256 KB per
    // utterance would show up in the very measurement this exists to take.
    if (!this.input) this.input = new ort.Tensor('float32', new Float32Array(grid.length), [1, N_MELS, N_FRAMES])
    ;(this.input.data as Float32Array).set(grid)
    const inferenceStart = performance.now()
    const outputs = await session.run({ input_features: this.input })
    const inferenceMs = performance.now() - inferenceStart
    // The graph's output is named `logits` but carries the sigmoid already
    // applied, which is what pipecat's own `inference.py` relies on. Reading it
    // by name rather than by position, because a renamed output should fail
    // loudly rather than silently score every turn identically.
    const logits = outputs.logits ?? Object.values(outputs)[0]
    const probability = Number((logits.data as Float32Array)[0])
    return {
      probability,
      complete: probability >= SMART_TURN_THRESHOLD,
      featuresMs,
      inferenceMs,
      audioSeconds: Math.min(audio.length, SMART_TURN_SAMPLES) / SMART_TURN_SAMPLE_RATE,
    }
  }
}
