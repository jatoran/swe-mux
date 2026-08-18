/**
 * Earcons: near-zero-latency acknowledgment sounds, synthesized in WebAudio.
 *
 * A 350 ms endpoint plus a 1-2 s assistant turn feels dead without an instant
 * acknowledgment; a bundled asset would work, but an oscillator blip needs no
 * file, no license, and no fetch. All earcons are quiet and short by design —
 * they mark state, they never compete with speech playback.
 */

type EarconKind = 'heard' | 'tick' | 'done' | 'error'

let context: AudioContext | null = null

function ensureContext(): AudioContext | null {
  try {
    context = context || new AudioContext()
    if (context.state === 'suspended') void context.resume()
    return context
  } catch {
    return null
  }
}

const SHAPES: Record<EarconKind, { frequencies: number[]; duration: number; gain: number }> = {
  heard: { frequencies: [660, 880], duration: 0.07, gain: 0.06 },
  tick: { frequencies: [1320], duration: 0.03, gain: 0.05 },
  done: { frequencies: [880, 660], duration: 0.09, gain: 0.05 },
  error: { frequencies: [220], duration: 0.15, gain: 0.06 },
}

export function playEarcon(kind: EarconKind): void {
  const audio = ensureContext()
  if (!audio) return
  const shape = SHAPES[kind]
  const now = audio.currentTime
  shape.frequencies.forEach((frequency, index) => {
    const oscillator = audio.createOscillator()
    const gain = audio.createGain()
    const start = now + index * shape.duration
    oscillator.type = 'sine'
    oscillator.frequency.value = frequency
    gain.gain.setValueAtTime(0, start)
    gain.gain.linearRampToValueAtTime(shape.gain, start + 0.008)
    gain.gain.exponentialRampToValueAtTime(0.0001, start + shape.duration)
    oscillator.connect(gain).connect(audio.destination)
    oscillator.start(start)
    oscillator.stop(start + shape.duration + 0.02)
  })
}
