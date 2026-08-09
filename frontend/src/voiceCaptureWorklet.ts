/**
 * The capture-side AudioWorklet: pull microphone blocks on the audio thread and
 * hand them to the main thread in ~32 ms batches.
 *
 * `ScriptProcessorNode` runs its callback on the main thread, so every frame's
 * arrival time — and therefore every endpointing decision — was jittered by
 * whatever the UI happened to be doing. A worklet is pulled by the audio thread on
 * a fixed schedule and cannot be delayed by a terminal repaint.
 *
 * The worklet deliberately does no arithmetic beyond batching. Resampling and
 * framing stay in `audioFrames.ts`, shared with the `ScriptProcessorNode` fallback,
 * so there is one implementation of each rather than one per capture source.
 *
 * The source is a string compiled at runtime through a blob URL rather than a
 * separate entry point, because `audioWorklet.addModule` needs a URL to a plain
 * JavaScript module and neither Vite's dev server nor its build emits one for a
 * TypeScript file without extra build configuration to keep in sync.
 */

export const CAPTURE_WORKLET_NAME = 'mux-voice-capture'

/** ~32 ms of input-rate audio per message: 375 messages/s would be pure waste. */
const BLOCKS_PER_MESSAGE = 12

const WORKLET_SOURCE = `
class MuxVoiceCapture extends AudioWorkletProcessor {
  constructor() {
    super()
    this.blocks = []
    this.length = 0
  }
  process(inputs) {
    const channel = inputs[0] && inputs[0][0]
    // An input with no connected source yields an empty array. Returning true keeps
    // the processor alive across that gap; returning false would retire it
    // permanently the first time the microphone stalls.
    if (!channel || channel.length === 0) return true
    // The output is left as the silence it arrives as. The node still declares one
    // and is connected through a muted gain to the destination, because the audio
    // graph is pulled from the destination backwards: a node with nothing
    // downstream is not guaranteed to be rendered at all.
    this.blocks.push(channel.slice())
    this.length += channel.length
    if (this.blocks.length < ${BLOCKS_PER_MESSAGE}) return true
    const batch = new Float32Array(this.length)
    let offset = 0
    for (const block of this.blocks) { batch.set(block, offset); offset += block.length }
    this.blocks = []
    this.length = 0
    this.port.postMessage(batch, [batch.buffer])
    return true
  }
}
registerProcessor(${JSON.stringify(CAPTURE_WORKLET_NAME)}, MuxVoiceCapture)
`

let moduleUrl: string | null = null

/** Blob URL for the worklet module, created once per document. */
export function captureWorkletUrl(): string {
  if (!moduleUrl) {
    moduleUrl = URL.createObjectURL(new Blob([WORKLET_SOURCE], { type: 'application/javascript' }))
  }
  return moduleUrl
}
