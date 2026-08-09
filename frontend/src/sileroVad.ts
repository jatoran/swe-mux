import { VAD_FRAME_SAMPLES, VAD_SAMPLE_RATE } from './audioFrames.ts'

/**
 * Silero VAD v5 over onnxruntime-web, as the speech detector for hands-free capture.
 *
 * The runtime and the 2.3 MB model are split into build assets and fetched lazily
 * on the first Talk start: the WASM runtime alone is ~11 MB and most sessions never
 * dictate. A failure to load is not fatal — capture falls back to
 * the energy detector and its long tail, because a microphone that refuses to open
 * is worse than one that endpoints slowly.
 *
 * Single-threaded on purpose. Multi-threaded WASM needs `SharedArrayBuffer`, which
 * needs COOP/COEP headers, which swe-mux does not send and cannot send on the
 * Tailscale Serve path. Asking for threads would fail at runtime on exactly the
 * device (a phone) where hands-free matters most.
 */

type OrtModule = typeof import('onnxruntime-web')
type Session = Awaited<ReturnType<OrtModule['InferenceSession']['create']>>

/** Silero v5 carries one recurrent state tensor of this shape between frames. */
const STATE_SHAPE = [2, 1, 128] as const
const STATE_SIZE = STATE_SHAPE[0] * STATE_SHAPE[1] * STATE_SHAPE[2]

export class SileroVad {
  private ort: OrtModule | null = null
  private session: Session | null = null
  private state = new Float32Array(STATE_SIZE)
  private rate: InstanceType<OrtModule['Tensor']> | null = null

  get ready(): boolean { return this.session !== null }

  async load(): Promise<void> {
    if (this.session) return
    // Every import is dynamic, and not only for laziness: the unit suite runs these
    // modules under plain Node, where neither the runtime nor an `?url` asset import
    // exists. Nothing here is evaluated until Talk is actually started.
    //
    // The runtime's own files are reached by a relative path into `node_modules`
    // rather than by package specifier, because onnxruntime-web's exports map does
    // not expose them and a bundler therefore refuses to resolve
    // `onnxruntime-web/dist/…`. Leaving `wasmPaths` unset is not the alternative it
    // appears to be: the runtime then resolves its `.wasm` against `import.meta.url`,
    // which the production build rewrites correctly but the dev server does not —
    // the request falls through to the SPA fallback and the runtime tries to
    // instantiate `index.html` as WebAssembly. `test/renderer/voice-capture.spec.ts`
    // pins this, because the failure degrades silently to the energy detector.
    const [ort, modelUrl, wasmUrl, glueUrl] = await Promise.all([
      import('onnxruntime-web'),
      import('@ricky0123/vad-web/dist/silero_vad_v5.onnx?url').then(module => module.default),
      import('../node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.wasm?url')
        .then(module => module.default),
      import('../node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.mjs?url')
        .then(module => module.default),
    ])
    ort.env.wasm.numThreads = 1
    ort.env.wasm.proxy = false
    ort.env.wasm.wasmPaths = { wasm: wasmUrl, mjs: glueUrl }
    ort.env.logLevel = 'error'
    const session = await ort.InferenceSession.create(modelUrl, {
      executionProviders: ['wasm'],
      // One frame at a time from a live microphone: there is nothing to parallelize
      // across, and extra threads only add scheduling latency to a 1 ms inference.
      interOpNumThreads: 1,
      intraOpNumThreads: 1,
    })
    this.ort = ort
    this.session = session
    this.rate = new ort.Tensor('int64', BigInt64Array.from([BigInt(VAD_SAMPLE_RATE)]), [])
    this.reset()
  }

  /**
   * Speech probability for one 512-sample frame.
   *
   * Throws if the frame is the wrong length rather than padding it: Silero's
   * output is meaningless on a short frame, and a silently wrong probability would
   * surface as an endpointing bug nobody could trace back to here.
   */
  async probability(frame: Float32Array): Promise<number> {
    const ort = this.ort
    const session = this.session
    if (!ort || !session || !this.rate) throw new Error('Silero VAD is not loaded')
    if (frame.length !== VAD_FRAME_SAMPLES) {
      throw new Error(`Silero VAD needs ${VAD_FRAME_SAMPLES}-sample frames, got ${frame.length}`)
    }
    const output = await session.run({
      input: new ort.Tensor('float32', frame, [1, frame.length]),
      state: new ort.Tensor('float32', this.state, [...STATE_SHAPE]),
      sr: this.rate,
    })
    // `Float32Array<ArrayBufferLike>` from the runtime: copied rather than cast so
    // the retained state can never alias a buffer the runtime reuses next frame.
    this.state = Float32Array.from(output.stateN.data as ArrayLike<number>)
    return Number((output.output.data as ArrayLike<number>)[0])
  }

  /** Clear the recurrent state between utterances so one does not colour the next. */
  reset(): void {
    this.state = new Float32Array(STATE_SIZE)
  }

  dispose(): void {
    void this.session?.release?.()
    this.session = null
    this.ort = null
    this.rate = null
  }
}
