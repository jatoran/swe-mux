/**
 * Demo-build stand-in for `onnxruntime-web`. Voice capture is switched off in
 * the demo config, so nothing ever reaches this module at runtime; it exists so
 * the 11 MB WASM runtime and its glue never enter the `site/demo/` bundle.
 * If something does call it, it fails the same way a failed runtime load does -
 * and the callers already degrade gracefully on that path.
 */
const refuse = (): never => { throw new Error('onnxruntime is not part of the demo build') }

export const env = {
  wasm: { numThreads: 1, proxy: false, wasmPaths: undefined as unknown },
  logLevel: 'error',
}

export const InferenceSession = { create: refuse }

export class Tensor {
  constructor() { refuse() }
}

export default { env, InferenceSession, Tensor }
