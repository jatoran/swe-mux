import { VAD_FRAME_MS } from './audioFrames.ts'

/**
 * The endpointing state machine, driven by per-frame speech probabilities.
 *
 * Frame-counted rather than clock-driven so it is deterministic and testable: a
 * decision that depended on wall time would move whenever inference fell a frame
 * behind, which is exactly when the endpoint matters most.
 *
 * It is deliberately separate from both the detector and the capture plumbing —
 * Silero and the energy fallback produce probabilities differently but must end an
 * utterance by the same rules.
 */

export type GateEvent =
  | { type: 'speech-start' }
  /** Enough trailing silence to start a decode that will be thrown away if speech resumes. */
  | { type: 'speculate' }
  /** Speech came back; anything speculated for this utterance is void. */
  | { type: 'resume' }
  | { type: 'endpoint'; reason: 'silence' | 'cap' }

export type GateConfig = {
  /** Probability at or above which a frame counts as speech. */
  speechThreshold: number
  /** Probability below which a frame counts as silence. Hysteresis: keep under the entry threshold. */
  silenceThreshold: number
  /** Trailing silence that ends the utterance. */
  endpointSilenceMs: number
  /** Trailing silence that starts a speculative decode. 0 disables speculation. */
  speculativeSilenceMs: number
  /** Speech required before an endpoint is allowed, so a cough is not an utterance. */
  minSpeechMs: number
  /** Hard cap; a monologue has to be cut somewhere. */
  maxUtteranceMs: number
}

/**
 * Silero separates speech from breath and room noise reliably, so the tail exists
 * only to cover a real pause between words rather than to ride out a detector that
 * false-triggers. That is the whole reason this can be 352 ms where the energy
 * detector needed 900.
 */
export const SILERO_GATE: GateConfig = {
  speechThreshold: 0.5,
  silenceThreshold: 0.35,
  endpointSilenceMs: 11 * VAD_FRAME_MS, // 352 ms
  // Deliberately well under the endpoint rather than the roadmap's "~300 ms": a
  // decode begun at 300 ms cannot finish before a 352 ms endpoint, which would
  // leave the grammar short-circuit unable to ever fire. Starting at 160 ms lets a
  // ~100 ms command decode land while there is still tail left to skip.
  speculativeSilenceMs: 5 * VAD_FRAME_MS, // 160 ms
  minSpeechMs: 224,
  maxUtteranceMs: 30_000,
}

/**
 * The fallback when Silero cannot load. The long tail is not a preference: an
 * RMS-over-noise-floor detector false-triggers on breath, so a short tail cuts
 * words in half. Speculation is off because the same false triggers would make
 * most speculative decodes garbage.
 */
export const ENERGY_GATE: GateConfig = {
  speechThreshold: 0.5,
  silenceThreshold: 0.5,
  endpointSilenceMs: 900,
  speculativeSilenceMs: 0,
  minSpeechMs: 220,
  maxUtteranceMs: 30_000,
}

export class SpeechGate {
  private readonly config: GateConfig
  /**
   * Optional extra patience, consulted per frame: the silence endpoint fires at
   * max(config tail, provider()). Chat mode uses it to give thinking-out-loud
   * room to breathe without touching command latency — speculation still fires
   * at its own threshold, so a wake-worded command short-circuits the longer
   * tail. Per-frame (not construction-time) so an addressee change between or
   * even during utterances takes effect immediately; only the compared
   * threshold moves, never the accumulated counters.
   */
  private readonly extraTail?: () => number
  private active = false
  private silence = 0
  private speech = 0
  private total = 0
  private speculated = false
  /**
   * Latched by the endpoint: an ended utterance never re-announces. The capture
   * layer replaces the gate the moment an endpoint fires, so live frames never
   * reach an ended gate — but a class that would scream `endpoint` once per
   * silent frame if they did is a footgun for every other caller and test.
   */
  private ended = false

  constructor(config: GateConfig, extraTail?: () => number) {
    this.config = config
    this.extraTail = extraTail
  }

  get speaking(): boolean { return this.active }
  get silenceMs(): number { return this.silence }
  get speechMs(): number { return this.speech }
  get totalMs(): number { return this.total }
  /** True once a speculative decode has been asked for and not yet invalidated. */
  get speculating(): boolean { return this.speculated }

  push(probability: number): GateEvent[] {
    const events: GateEvent[] = []
    if (this.ended) return events
    if (!this.active) {
      if (probability < this.config.speechThreshold) return events
      this.active = true
      this.silence = 0
      this.speech = VAD_FRAME_MS
      this.total = VAD_FRAME_MS
      this.speculated = false
      events.push({ type: 'speech-start' })
      return events
    }
    this.total += VAD_FRAME_MS
    if (probability >= this.config.silenceThreshold) {
      // Speech resumed. A decode already in flight was transcribing a prefix of
      // this utterance, so its result is void — not merely stale, because acting
      // on it would submit half a sentence.
      if (this.speculated) {
        this.speculated = false
        events.push({ type: 'resume' })
      }
      this.silence = 0
      this.speech += VAD_FRAME_MS
    } else {
      this.silence += VAD_FRAME_MS
    }
    if (this.total >= this.config.maxUtteranceMs) {
      this.ended = true
      events.push({ type: 'endpoint', reason: 'cap' })
      return events
    }
    if (this.speech < this.config.minSpeechMs) return events
    if (this.silence >= Math.max(this.config.endpointSilenceMs, this.extraTail?.() || 0)) {
      this.ended = true
      events.push({ type: 'endpoint', reason: 'silence' })
      return events
    }
    if (
      this.config.speculativeSilenceMs > 0
      && !this.speculated
      && this.silence >= this.config.speculativeSilenceMs
    ) {
      this.speculated = true
      events.push({ type: 'speculate' })
    }
    return events
  }

  reset(): void {
    this.active = false
    this.silence = 0
    this.speech = 0
    this.total = 0
    this.speculated = false
    this.ended = false
  }
}
