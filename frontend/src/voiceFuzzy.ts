/**
 * Tier 2 of voice routing: a token-level fuzzy pass over the same compiled
 * grammar the deterministic resolver uses. It exists to absorb STT noise
 * ("shesh in three" for "session three") before an utterance costs a model
 * call, and it deliberately stays conservative: a weak match falls through to
 * the assistant rather than firing the wrong command.
 *
 * Only alias phrases without `{text}` slots participate. A slot template that
 * fuzzy-matched could capture the wrong span as its argument, which turns a
 * misheard word into a mis-*targeted* action — the failure the deterministic
 * tier exists to prevent.
 */
import type { Command } from './commands.ts'
import { normalizeSpokenText } from './voiceIntents.ts'

export type FuzzyVoiceMatch = {
  command: Command
  phrase: string
  score: number
}

/** Damerau-ish edit distance, bounded for short spoken tokens. */
export function editDistance(left: string, right: string): number {
  if (left === right) return 0
  const rows = left.length + 1
  const cols = right.length + 1
  const table: number[] = new Array(rows * cols)
  for (let row = 0; row < rows; row++) table[row * cols] = row
  for (let col = 0; col < cols; col++) table[col] = col
  for (let row = 1; row < rows; row++) {
    for (let col = 1; col < cols; col++) {
      const substitution = left[row - 1] === right[col - 1] ? 0 : 1
      let best = Math.min(
        table[(row - 1) * cols + col] + 1,
        table[row * cols + col - 1] + 1,
        table[(row - 1) * cols + col - 1] + substitution,
      )
      if (
        row > 1 && col > 1
        && left[row - 1] === right[col - 2]
        && left[row - 2] === right[col - 1]
      ) {
        best = Math.min(best, table[(row - 2) * cols + col - 2] + 1)
      }
      table[row * cols + col] = best
    }
  }
  return table[rows * cols - 1]
}

function tokenSimilarity(spoken: string, target: string): number {
  if (spoken === target) return 1
  const longest = Math.max(spoken.length, target.length)
  if (!longest) return 0
  const distance = editDistance(spoken, target)
  return 1 - distance / longest
}

/**
 * Phrase similarity: aligned token-by-token average. Alignment is positional
 * because spoken commands are short and word order is what the grammar means;
 * a bag-of-words match would let "send project two" hit "project two send".
 */
export function phraseSimilarity(spoken: string, phrase: string): number {
  const spokenTokens = spoken.split(/\s+/).filter(Boolean)
  const phraseTokens = phrase.split(/\s+/).filter(Boolean)
  if (!spokenTokens.length || !phraseTokens.length) return 0
  if (Math.abs(spokenTokens.length - phraseTokens.length) > 1) return 0
  const length = Math.max(spokenTokens.length, phraseTokens.length)
  let total = 0
  for (let index = 0; index < length; index++) {
    total += tokenSimilarity(spokenTokens[index] || '', phraseTokens[index] || '')
  }
  return total / length
}

// High enough that "send" cannot fuzzy-fire "end", low enough that one
// misrecognized syllable inside a three-word phrase still lands.
export const FUZZY_THRESHOLD = 0.78
// A runner-up this close means the utterance is genuinely ambiguous between
// two commands, and guessing between them is worse than falling through.
const AMBIGUITY_MARGIN = 0.06

export function resolveVoiceFuzzy(commands: Command[], spoken: string): FuzzyVoiceMatch | null {
  const normalized = normalizeSpokenText(spoken)
  if (!normalized || normalized.split(/\s+/).length > 8) return null
  let best: FuzzyVoiceMatch | null = null
  let runnerUp = 0
  for (const command of commands) {
    if (!command.available || !command.voice) continue
    for (const phrase of command.voice.phrases) {
      if (phrase.includes('{text}')) continue
      const normalizedPhrase = normalizeSpokenText(phrase)
      // An exact match belongs to tier 1; scoring it here would double-fire.
      if (!normalizedPhrase || normalizedPhrase === normalized) continue
      const score = phraseSimilarity(normalized, normalizedPhrase)
      if (!best || score > best.score) {
        runnerUp = best && best.command.id !== command.id ? best.score : runnerUp
        best = { command, phrase, score }
      } else if (command.id !== best.command.id && score > runnerUp) {
        runnerUp = score
      }
    }
  }
  if (!best || best.score < FUZZY_THRESHOLD) return null
  if (best.score - runnerUp < AMBIGUITY_MARGIN && runnerUp > 0) return null
  return best
}
