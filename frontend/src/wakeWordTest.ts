import type { MuxVoiceCommand, VoiceMatcher } from './conversation'

/**
 * Measuring whether a wake word survives the recognizer.
 *
 * Wake-word choice is an ASR problem wearing a configuration problem's clothes: a
 * trigger the model has never seen comes back as three different words and the
 * matcher, which invents no variants, silently never fires. The only way to choose
 * one is to speak it and read what the decoder actually produced — so a trial holds
 * the raw transcript, not just a pass/fail.
 *
 * Everything here is pure. The recognizer and the matcher are the real ones; this
 * only scores their output.
 */

export type WakeWordTrial = {
  /** Exactly what the recognizer produced, which is the point of the exercise. */
  text: string
  /** The action the live matcher fired, or null when the utterance was left as draft. */
  command: MuxVoiceCommand | null
  /** The configured wake word heard anywhere in the transcript, or null. */
  wake: string | null
  decodeMs: number
}

export type WakeWordReport = {
  total: number
  /** Trials where the suffix grammar fired: wake word plus a known phrase, at the end. */
  matched: number
  /** Trials where a wake word was heard at all. The gap to `matched` is a phrase problem. */
  wakeHeard: number
  byCommand: { command: string; count: number }[]
  /**
   * Transcripts where no configured wake word was heard. These are the variants to
   * add, or the evidence that the trigger word itself is the wrong choice.
   */
  misses: string[]
  medianDecodeMs: number
}

const normalize = (value: string): string => value.replace(/\s+/g, ' ').trim().toLowerCase()
const escapeRegex = (value: string): string => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/**
 * The first configured wake word heard anywhere in the transcript.
 *
 * Whole words only: without the boundaries, "swe" matches inside "sweet" and the
 * report would claim the trigger was heard in exactly the case that proves it was
 * not. Longest first, so a variant that contains a shorter one wins.
 */
export function findWakeWord(text: string, wakeWords: string[]): string | null {
  const cleaned = normalize(text)
  if (!cleaned) return null
  const candidates = [...new Set(wakeWords.map(normalize).filter(Boolean))]
    .sort((a, b) => b.length - a.length)
  for (const word of candidates) {
    if (new RegExp(`\\b${escapeRegex(word).replace(/\s+/g, '\\s+')}\\b`, 'i').test(cleaned)) return word
  }
  return null
}

export function evaluateTrial(
  text: string,
  matcher: VoiceMatcher,
  wakeWords: string[],
  decodeMs = 0,
): WakeWordTrial {
  const cleaned = text.replace(/\s+/g, ' ').trim()
  return {
    text: cleaned,
    command: matcher.parse(cleaned).command,
    wake: findWakeWord(cleaned, wakeWords),
    decodeMs: Math.max(0, Math.round(decodeMs)),
  }
}

export function summarizeTrials(trials: WakeWordTrial[]): WakeWordReport {
  const counts = new Map<string, number>()
  for (const trial of trials) {
    if (!trial.command) continue
    counts.set(trial.command, (counts.get(trial.command) || 0) + 1)
  }
  const decodes = trials.map(trial => trial.decodeMs).sort((a, b) => a - b)
  return {
    total: trials.length,
    matched: trials.filter(trial => trial.command).length,
    wakeHeard: trials.filter(trial => trial.wake).length,
    byCommand: [...counts.entries()]
      .map(([command, count]) => ({ command, count }))
      .sort((a, b) => b.count - a.count || a.command.localeCompare(b.command)),
    misses: trials.filter(trial => !trial.wake).map(trial => trial.text),
    medianDecodeMs: decodes.length ? decodes[Math.floor((decodes.length - 1) / 2)] : 0,
  }
}
