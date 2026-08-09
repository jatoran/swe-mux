import { VAD_FRAME_SAMPLES, VAD_SAMPLE_RATE } from '../../src/audioFrames.ts'
import { SileroVad } from '../../src/sileroVad.ts'
import { captureWorkletUrl, CAPTURE_WORKLET_NAME } from '../../src/voiceCaptureWorklet.ts'

/**
 * Browser-side harness for the two pieces of capture that unit tests cannot reach:
 * the ONNX runtime actually loading and answering, and the AudioWorklet actually
 * being pulled by the audio graph.
 *
 * Both fail silently in production if they are wrong — a VAD that never loads falls
 * back to the energy detector, and a worklet the graph never renders simply produces
 * no utterances — so neither has a symptom that points at its own cause.
 */

declare global {
  interface Window {
    muxVoiceHarness: {
      sileroProbabilities(): Promise<{ speech: number; silence: number }>
      workletDelivers(): Promise<number>
    }
  }
}

/** A 512-sample frame of band-limited noise, which Silero should rate as speech-like. */
function voiced(seed: number): Float32Array {
  const frame = new Float32Array(VAD_FRAME_SAMPLES)
  for (let index = 0; index < frame.length; index++) {
    const t = index / VAD_SAMPLE_RATE
    frame[index] = 0.35 * Math.sin(2 * Math.PI * (120 + seed * 7) * t)
      + 0.25 * Math.sin(2 * Math.PI * 480 * t)
      + 0.15 * Math.sin(2 * Math.PI * 1_450 * t)
  }
  return frame
}

window.muxVoiceHarness = {
  async sileroProbabilities() {
    const vad = new SileroVad()
    await vad.load()
    let speech = 0
    for (let index = 0; index < 12; index++) speech = await vad.probability(voiced(index))
    vad.reset()
    let silence = 0
    for (let index = 0; index < 12; index++) silence = await vad.probability(new Float32Array(VAD_FRAME_SAMPLES))
    vad.dispose()
    return { speech, silence }
  },

  async workletDelivers() {
    const context = new AudioContext({ sampleRate: 48_000 })
    await context.audioWorklet.addModule(captureWorkletUrl())
    const oscillator = context.createOscillator()
    const node = new AudioWorkletNode(context, CAPTURE_WORKLET_NAME, {
      numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1],
      channelCount: 1, channelCountMode: 'explicit',
    })
    const sink = context.createGain()
    sink.gain.value = 0
    sink.connect(context.destination)
    let samples = 0
    node.port.onmessage = event => { samples += (event.data as Float32Array).length }
    oscillator.connect(node)
    node.connect(sink)
    oscillator.start()
    await new Promise(resolve => setTimeout(resolve, 600))
    oscillator.stop()
    await context.close()
    return samples
  },
}
